"""Local authentication. Retry only rolled-back, pre-commit transaction stages."""
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from hmac import compare_digest

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from backend.app.core.identidad import normalizar_nombre_usuario
from backend.app.core.sesiones import digest, nuevo_token, utc_now
from backend.app.models.autenticacion import (
    AuditoriaAutenticacion, CredencialSesion, LimiteAutenticacion, SesionUsuario,
)
from backend.app.models.usuario import Usuario
from backend.app.services.seguridad_service import ContextoSeguridad, politica
from backend.app.services.usuario_service import UsuarioService


MAX_AUTH_TRANSACTION_ATTEMPTS = 3


def _is_retryable_auth_conflict(error, dialect):
    if (dialect not in {"mysql", "mariadb"} or not isinstance(error, OperationalError)
            or error.connection_invalidated):
        return False
    args = getattr(error.orig, "args", ())
    return bool(args and type(args[0]) is int and args[0] in {1020, 1205, 1213})


def _retry_transaction_stage(operation):
    """A stage contains exactly one _tx; never wrap login/password admission together."""
    @wraps(operation)
    def run(self, *args, **kwargs):
        for attempt in range(MAX_AUTH_TRANSACTION_ATTEMPTS):
            self._transaction_replayable = False
            try:
                return operation(self, *args, **kwargs)
            except OperationalError as error:
                if (not self._transaction_replayable
                        or attempt + 1 == MAX_AUTH_TRANSACTION_ATTEMPTS
                        or not self._should_retry_transaction(error)):
                    raise
                # _tx already rolled back the whole root transaction. The next
                # attempt rediscovers IDs, locks rows and checks all current state.
    return run


class ErrorAutenticacion(Exception):
    def __init__(self, code="CREDENCIALES_INVALIDAS", status=401):
        self.code, self.status = code, status
        super().__init__(code)


class AutenticacionService:
    def __init__(self, db, *, contexto=None, origen="local", clock=utc_now):
        self.db = db
        self.contexto = contexto or ContextoSeguridad()
        self.origen = digest(origen)
        self.clock = clock
        self._attempt_user_id = None

    @contextmanager
    def _tx(self):
        if self.db.in_transaction():
            raise RuntimeError("Autenticacion requiere una transaccion independiente")
        self._transaction_replayable = False
        denial = None
        try:
            if self.db.get_bind().dialect.name == "sqlite":
                self.db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                self.db.begin()
            try:
                yield
            except ErrorAutenticacion as error:
                # Expected denials still persist throttles, locks and audit events.
                denial = error
            # Flush explicitly BEFORE the commit boundary. A snapshot conflict
            # here is known to be uncommitted, including audit/counter writes
            # deferred by autoflush=False and the expected-denial path.
            self.db.flush()
        except Exception:
            self.db.rollback()
            self._transaction_replayable = True  # Only after successful full rollback.
            raise
        # From this point on, no error is replayable, even an OperationalError
        # carrying 1020 or one raised after the server actually committed.
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        if denial is not None:
            raise denial

    def _should_retry_transaction(self, error):
        return _is_retryable_auth_conflict(error, self.db.get_bind().dialect.name)

    def _audit(self, evento, now, session=None, usuario_id=None, motivo=None):
        self.db.add(AuditoriaAutenticacion(
            evento=evento, fecha=now, origen_hash=self.origen,
            usuario_id=session.usuario_id if session else usuario_id,
            sesion_id=session.id if session else None, motivo=motivo,
        ))

    @_retry_transaction_stage
    def _admit(self, nombre):
        try:
            nombre = normalizar_nombre_usuario(nombre)
        except (ValueError, TypeError):
            nombre = "<invalido>"
        pair_key = digest("cuenta:" + self.origen + ":" + nombre)
        origin_key = digest("origen:" + self.origen)
        with self._tx():
            now = self.clock()
            for key in sorted((pair_key, origin_key)):
                values = dict(clave=key, intentos=0, generacion=1, ultimo_at=now)
                dialect = self.db.get_bind().dialect.name
                if dialect == "sqlite":
                    stmt = sqlite_insert(LimiteAutenticacion).values(**values).on_conflict_do_nothing()
                elif dialect in {"mysql", "mariadb"}:
                    stmt = mysql_insert(LimiteAutenticacion).values(**values).on_duplicate_key_update(clave=key)
                else:
                    raise RuntimeError("Motor de autenticacion no soportado")
                self.db.execute(stmt)
            buckets = {row.clave: row for row in self.db.scalars(
                select(LimiteAutenticacion).where(LimiteAutenticacion.clave.in_((pair_key, origin_key)))
                .order_by(LimiteAutenticacion.clave).with_for_update()
                .execution_options(populate_existing=True)
            )}
            # A policy SELECT establishes an InnoDB RR read view even when it
            # returns no rows. Do it AFTER the sorted upserts/locking reads,
            # so a waiter does not bring an older snapshot to quota rows.
            p = politica(self.db, self.contexto)
            for key in (origin_key, pair_key):
                bucket = buckets[key]
                window = p["ventana_origen"] if key == origin_key else p["reinicio_fallos"]
                reference = bucket.ultimo_at if key == origin_key else (bucket.ultimo_fallo_at or bucket.ultimo_at)
                if bucket.bloqueado_hasta is not None and now >= bucket.bloqueado_hasta:
                    bucket.intentos, bucket.bloqueado_hasta = 0, None
                    bucket.generacion += 1
                    bucket.ultimo_fallo_at = None
                elif bucket.bloqueado_hasta is None and now >= reference + timedelta(seconds=window):
                    bucket.intentos = 0
                    bucket.generacion += 1
                    bucket.ultimo_fallo_at = None
                limit = p["intentos_origen"] if key == origin_key else p["intentos_cuenta"]
                if bucket.bloqueado_hasta is not None or bucket.intentos >= limit:
                    self._audit("INTENTO_FALLIDO", now, motivo="LIMITE_ORIGEN" if key == origin_key else "LIMITE_CUENTA_ORIGEN")
                    raise ErrorAutenticacion("LIMITE_ORIGEN", 429) if key == origin_key else ErrorAutenticacion()
                # Charge origin first, including requests denied by the account quota.
                bucket.intentos += 1  # Reserve before Argon2; concurrent requests cannot bypass the quota.
                bucket.ultimo_at = now
                if key == pair_key and bucket.intentos >= p["intentos_cuenta"]:
                    bucket.bloqueado_hasta = now + timedelta(seconds=p["bloqueo_intentos"])
                elif key == origin_key and bucket.intentos >= p["intentos_origen"]:
                    bucket.bloqueado_hasta = now + timedelta(seconds=p["ventana_origen"])
            reservation = pair_key, buckets[pair_key].generacion
        return reservation

    def _success(self, reservation):
        pair_key, generation = reservation
        row = self.db.scalar(select(LimiteAutenticacion).where(
            LimiteAutenticacion.clave == pair_key).with_for_update().execution_options(populate_existing=True))
        if row.generacion != generation:
            return  # A slow completion must not decrement a newer window's reservations.
        row.intentos = max(0, row.intentos - 1)
        if row.intentos < politica(self.db, self.contexto)["intentos_cuenta"]:
            row.bloqueado_hasta = None

    def _failure(self, reservation, now):
        pair_key, generation = reservation
        row = self.db.scalar(select(LimiteAutenticacion).where(
            LimiteAutenticacion.clave == pair_key).with_for_update().execution_options(populate_existing=True))
        if row.generacion == generation:
            row.ultimo_fallo_at = now

    def _password(self, nombre, password):
        # Short read transaction, no row locks while doing Argon2.
        with self.db.begin():
            verified = UsuarioService(self.db).verificar_credenciales(nombre, password)
            self._attempt_user_id = verified.id if verified else None
            if verified is None:
                try:
                    normalized = normalizar_nombre_usuario(nombre)
                except (ValueError, TypeError):
                    normalized = None
                if normalized is not None:
                    # Internal audit association only; never change the public response.
                    self._attempt_user_id = self.db.scalar(select(Usuario.id).where(
                        Usuario.nombre_usuario == normalized))
            return verified

    def _user(self, user_id):
        return self.db.scalar(select(Usuario).where(Usuario.id == user_id)
                              .with_for_update().execution_options(populate_existing=True))

    def _invalidate(self, session, now, pending=False):
        stmt = select(CredencialSesion).where(CredencialSesion.sesion_id == session.id)
        if pending:
            stmt = stmt.where(CredencialSesion.estado == "PENDIENTE")
        for row in self.db.scalars(stmt.order_by(CredencialSesion.id).with_for_update()
                                   .execution_options(populate_existing=True)):
            if row.estado != "INVALIDADA":
                row.estado, row.invalidada_at = "INVALIDADA", now

    def _end(self, session, now, motivo, estado="REVOCADA"):
        session.estado, session.finalizada_at, session.motivo = estado, now, motivo
        self._invalidate(session, now)
        self._audit("CIERRE" if estado == "CERRADA" else "REVOCACION", now, session, motivo=motivo)

    def _session(self, session_id, now, *, allow_locked=False):
        # Discovery only; user -> session -> credentials is the locking order.
        user_id = self.db.scalar(select(SesionUsuario.usuario_id).where(SesionUsuario.id == session_id))
        if user_id is None:
            raise ErrorAutenticacion()
        user = self._user(user_id)
        session = self.db.scalar(select(SesionUsuario).where(SesionUsuario.id == session_id)
                                 .with_for_update().execution_options(populate_existing=True))
        if session is None or session.estado in {"CERRADA", "REVOCADA"}:
            raise ErrorAutenticacion()
        if (session.sucursal, session.puesto) != (self.contexto.sucursal, self.contexto.puesto):
            raise ErrorAutenticacion()
        if user is None or not user.activo or user.version != session.usuario_version:
            self._end(session, now, "IDENTIDAD_CAMBIADA")
            raise ErrorAutenticacion()
        p = politica(self.db, self.contexto)
        # Rolling recovery horizon, not an absolute lifetime since login.
        if now >= session.actividad_at + timedelta(seconds=p["recuperacion"]):
            self._end(session, now, "ABANDONO")
            raise ErrorAutenticacion()
        if session.estado == "ACTIVA" and now >= session.actividad_at + timedelta(seconds=p["inactividad"]):
            session.estado, session.bloqueada_at = "BLOQUEADA", now
            self._invalidate(session, now, pending=True)
            self._audit("BLOQUEO_INACTIVIDAD", now, session)
        if session.estado == "BLOQUEADA" and not allow_locked:
            raise ErrorAutenticacion("SESION_BLOQUEADA", 423)
        return session

    def _token(self, token, purpose, now, *, allow_locked=False):
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise ErrorAutenticacion()
        column = CredencialSesion.acceso_hash if purpose == "acceso" else CredencialSesion.renovacion_hash
        key = digest(token)
        session_id = self.db.scalar(select(CredencialSesion.sesion_id).where(column == key))
        if session_id is None:
            raise ErrorAutenticacion()
        session = self._session(session_id, now, allow_locked=True)
        row = self.db.scalar(select(CredencialSesion).where(column == key)
                             .with_for_update().execution_options(populate_existing=True))
        until = (row.acceso_hasta if purpose == "acceso" else row.renovacion_hasta) if row else None
        if (row is None or row.estado != "ACTIVA" or row.generacion != session.generacion
                or until is None or now >= until):
            raise ErrorAutenticacion()
        if session.estado == "BLOQUEADA" and not allow_locked:
            raise ErrorAutenticacion("SESION_BLOQUEADA", 423)
        return session, row

    def _pair(self, session, now, p, *, pending=False):
        access, refresh = nuevo_token(), nuevo_token()
        row = CredencialSesion(
            sesion_id=session.id, generacion=session.generacion + (1 if pending else 0),
            origen_generacion=session.generacion if pending else 0,
            acceso_hash=digest(access), renovacion_hash=digest(refresh),
            estado="PENDIENTE" if pending else "ACTIVA", creada_at=now,
            confirmar_hasta=now + timedelta(seconds=p["preparacion"]) if pending else None,
            activada_at=None if pending else now,
            acceso_hasta=None if pending else now + timedelta(seconds=p["acceso"]),
            renovacion_hasta=None if pending else now + timedelta(seconds=p["renovacion"]),
        )
        self.db.add(row)
        self.db.flush()
        return {"sesion_id": session.global_id, "rotacion_id": row.rotacion_id,
                "token_acceso": access, "token_renovacion": refresh,
                "confirmar_hasta": row.confirmar_hasta, "acceso_hasta": row.acceso_hasta,
                "renovacion_hasta": row.renovacion_hasta, "estado": row.estado}

    def login(self, nombre, password):
        key = self._admit(nombre)
        verified = self._password(nombre, password)
        return self._login_verificado(key, verified)

    @_retry_transaction_stage
    def _login_verificado(self, key, verified):
        with self._tx():
            now = self.clock()
            # A failed password for a known account still inserts an audit FK
            # to usuarios. Lock that parent BEFORE the quota row, just as on
            # success, rather than taking an implicit FK lock after the quota.
            user_id = verified.id if verified else self._attempt_user_id
            user = self._user(user_id) if user_id is not None else None
            if verified is None or user is None or not user.activo or user.version != verified.version:
                self._failure(key, now)
                self._audit("INTENTO_FALLIDO", now, usuario_id=user.id if user else None, motivo="CREDENCIALES")
                raise ErrorAutenticacion()
            self._success(key)
            session = SesionUsuario(usuario_id=user.id, usuario_version=user.version,
                                    sucursal=self.contexto.sucursal, puesto=self.contexto.puesto,
                                    estado="ACTIVA", generacion=1, creada_at=now, actividad_at=now)
            self.db.add(session)
            self.db.flush()
            result = self._pair(session, now, politica(self.db, self.contexto))
            self._audit("INICIO", now, session)
        return result

    def desbloquear(self, locator, nombre, password):
        key = self._admit(nombre)
        verified = self._password(nombre, password)
        return self._desbloquear_verificado(locator, key, verified)

    @_retry_transaction_stage
    def _desbloquear_verificado(self, locator, key, verified):
        with self._tx():
            now = self.clock()
            # A locator is never a bearer credential. Check ownership before revealing state.
            user_id = verified.id if verified else self._attempt_user_id
            user = self._user(user_id) if user_id is not None else None
            session_id = self.db.scalar(select(SesionUsuario.id).where(
                SesionUsuario.global_id == locator,
                SesionUsuario.usuario_id == user.id if user else False))
            if verified is None or user is None or not user.activo or user.version != verified.version or session_id is None:
                self._failure(key, now)
                self._audit("INTENTO_FALLIDO", now, usuario_id=user.id if user else None, motivo="DESBLOQUEO")
                raise ErrorAutenticacion()
            try:
                session = self._session(session_id, now, allow_locked=True)
            except ErrorAutenticacion:
                self._failure(key, now)
                self._audit("INTENTO_FALLIDO", now, usuario_id=self._attempt_user_id, motivo="DESBLOQUEO")
                raise ErrorAutenticacion() from None
            if session.estado != "BLOQUEADA":
                self._failure(key, now)
                self._audit("INTENTO_FALLIDO", now, usuario_id=self._attempt_user_id, motivo="DESBLOQUEO")
                raise ErrorAutenticacion()
            self._success(key)
            self._invalidate(session, now)
            session.generacion += 1
            session.estado, session.bloqueada_at, session.actividad_at = "ACTIVA", None, now
            result = self._pair(session, now, politica(self.db, self.contexto))
            self._audit("DESBLOQUEO", now, session)
        return result

    @_retry_transaction_stage
    def actual(self, token, *, actividad=False):
        with self._tx():
            now = self.clock()
            session, _ = self._token(token, "acceso", now, allow_locked=not actividad)
            if actividad:
                session.actividad_at = now
            result = {"sesion_id": session.global_id, "usuario_id": session.usuario_id,
                      "estado": session.estado, "creada_at": session.creada_at,
                      "actividad_at": session.actividad_at, "bloqueada_at": session.bloqueada_at}
        return result

    @_retry_transaction_stage
    def preparar(self, token):
        with self._tx():
            now = self.clock()
            session, _ = self._token(token, "renovacion", now)
            self._invalidate(session, now, pending=True)
            result = self._pair(session, now, politica(self.db, self.contexto), pending=True)
            # No change to old expiry, activity, recovery horizon or generation.
            self._audit("RENOVACION_PREPARADA", now, session)
        return result

    @_retry_transaction_stage
    def confirmar(self, rotation, token):
        with self._tx():
            now = self.clock()
            if not isinstance(token, str) or not 40 <= len(token) <= 128:
                raise ErrorAutenticacion()
            session_id = self.db.scalar(select(CredencialSesion.sesion_id).where(
                CredencialSesion.rotacion_id == rotation, CredencialSesion.renovacion_hash == digest(token)))
            if session_id is None:
                raise ErrorAutenticacion()
            session = self._session(session_id, now)
            row = self.db.scalar(select(CredencialSesion).where(CredencialSesion.rotacion_id == rotation)
                                 .with_for_update().execution_options(populate_existing=True))
            if row is None or not compare_digest(row.renovacion_hash, digest(token)):
                raise ErrorAutenticacion()
            if row.estado == "ACTIVA":
                if row.generacion != session.generacion or now >= row.renovacion_hasta:
                    raise ErrorAutenticacion()
                # Idempotent acknowledgement: never extend deadlines or issue tokens.
            elif row.estado == "PENDIENTE":
                parent = self.db.scalar(select(CredencialSesion).where(
                    CredencialSesion.sesion_id == session.id, CredencialSesion.estado == "ACTIVA",
                    CredencialSesion.generacion == row.origen_generacion,
                ).with_for_update().execution_options(populate_existing=True))
                if (now >= row.confirmar_hasta or row.origen_generacion != session.generacion
                        or parent is None or now >= parent.renovacion_hasta):
                    raise ErrorAutenticacion()
                self._invalidate(session, now)
                p = politica(self.db, self.contexto)
                row.estado, row.invalidada_at, row.activada_at = "ACTIVA", None, now
                row.acceso_hasta = now + timedelta(seconds=p["acceso"])
                row.renovacion_hasta = now + timedelta(seconds=p["renovacion"])
                session.generacion = row.generacion
                self._audit("RENOVACION", now, session)
            else:
                raise ErrorAutenticacion()
            result = {"estado": "ACTIVA", "acceso_hasta": row.acceso_hasta,
                      "renovacion_hasta": row.renovacion_hasta}
        return result

    @_retry_transaction_stage
    def logout(self, token):
        with self._tx():
            now = self.clock()
            session, _ = self._token(token, "renovacion", now, allow_locked=True)
            self._end(session, now, "SALIDA", estado="CERRADA")

    @_retry_transaction_stage
    def revocar(self, locator, motivo="SEGURIDAD"):
        """Trusted internal port for future revocations. No public admin endpoint."""
        if motivo not in {"SEGURIDAD", "SINCRONIZACION", "ADMINISTRACION"}:
            raise ValueError("Motivo de revocacion invalido")
        with self._tx():
            now = self.clock()
            session_id = self.db.scalar(select(SesionUsuario.id).where(SesionUsuario.global_id == locator))
            session = self._session(session_id, now, allow_locked=True)
            self._end(session, now, motivo)
