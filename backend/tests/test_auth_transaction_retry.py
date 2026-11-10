"""C2 recovery: SQLite + injected PyMySQL errors; never connect to MariaDB."""
from unittest.mock import Mock

from pymysql.err import OperationalError as DriverOperationalError
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.app.models import AuditoriaAutenticacion, CredencialSesion, LimiteAutenticacion, SesionUsuario, Usuario
from backend.app.services.autenticacion_service import (
    AutenticacionService, ErrorAutenticacion, MAX_AUTH_TRANSACTION_ATTEMPTS,
    _is_retryable_auth_conflict,
)
from backend.tests.test_sprint_9_5c_c2_autenticacion import (
    h, password_hash, PASSWORD, WRONG,  # noqa: F401
)


def sql_error(code=1020, invalidated=False):
    return OperationalError("SQL simulado", {}, DriverOperationalError(code, "conflicto simulado"),
                            connection_invalidated=invalidated)


def use_driver_policy(auth, monkeypatch):
    # Do not change SQLite isolation/engine; simulate only error classification.
    monkeypatch.setattr(auth, "_should_retry_transaction", lambda error: _is_retryable_auth_conflict(error, "mysql"))


def is_lock(state, model):
    return (state.is_select and state.statement._for_update_arg is not None
            and state.statement.column_descriptions[0]["expr"] is model)


def setup_operation(h, auth, action):
    pair = h.login()
    if action == "confirmar":
        candidate = h.call("preparar", pair["token_renovacion"])
        return pair, lambda: auth.confirmar(candidate["rotacion_id"], candidate["token_renovacion"])
    if action == "preparar":
        return pair, lambda: auth.preparar(pair["token_renovacion"])
    if action == "logout":
        return pair, lambda: auth.logout(pair["token_renovacion"])
    return pair, lambda: auth.revocar(pair["sesion_id"])


@pytest.mark.parametrize("action", ["confirmar", "preparar", "logout", "revocar"])
@pytest.mark.parametrize("target", [SesionUsuario, CredencialSesion])
@pytest.mark.parametrize("code", [1020, 1205, 1213])
def test_c2_conflicto_de_bloqueo_repite_transaccion_completa(h, monkeypatch, action, target, code):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        pair, operate = setup_operation(h, auth, action)
        use_driver_policy(auth, monkeypatch)
        roots, hits = [], []
        event.listen(db, "after_transaction_create", lambda session, tx: roots.append(tx) if tx.parent is None else None)
        def fail_once(state):
            if is_lock(state, target):
                hits.append(True)
                if len(hits) == 1:
                    # Prove rollback removes already-flushed changes, not just Python state.
                    auth._audit("DESCARTADO", h.now)
                    db.flush()
                    raise sql_error(code)
        event.listen(db, "do_orm_execute", fail_once)
        rollback = Mock(wraps=db.rollback)
        commit = Mock(wraps=db.commit)
        monkeypatch.setattr(db, "rollback", rollback)
        monkeypatch.setattr(db, "commit", commit)
        operate()
        assert len(roots) == 2 and roots[0] is not roots[1]
        rollback.assert_called_once()
        commit.assert_called_once()
        assert db.get_transaction() is None
    assert h.events("DESCARTADO") == []
    expected = {"confirmar": "RENOVACION", "preparar": "RENOVACION_PREPARADA",
                "logout": "CIERRE", "revocar": "REVOCACION"}[action]
    assert len(h.events(expected)) == 1
    if action in {"logout", "revocar"}:
        assert h.session(pair["sesion_id"]).estado in {"CERRADA", "REVOCADA"}


@pytest.mark.parametrize("winner", ["confirmar", "preparar", "logout", "revocar"])
def test_c2_1020_confirmacion_relee_ganador_sin_reactivar_credenciales(h, monkeypatch, winner):
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        use_driver_policy(auth, monkeypatch)
        hits, competitors, roots = [], [], []
        event.listen(db, "after_transaction_create", lambda session, tx: roots.append(tx) if tx.parent is None else None)
        def fail_once(state):
            if is_lock(state, CredencialSesion):
                hits.append(True)
                if len(hits) == 1:
                    raise sql_error()
        event.listen(db, "do_orm_execute", fail_once)
        real_rollback = db.rollback
        def rollback_then_compete():
            real_rollback()
            if competitors:
                return
            competitors.append(True)
            # Commit a competing result in another session, AFTER releasing the
            # SQLite write lock, to model what the refreshed RR view must see.
            if winner == "confirmar":
                h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
            elif winner == "revocar":
                h.call("revocar", pair["sesion_id"])
            else:
                h.call(winner, pair["token_renovacion"])
        monkeypatch.setattr(db, "rollback", rollback_then_compete)
        if winner == "confirmar":
            result = auth.confirmar(pending["rotacion_id"], pending["token_renovacion"])
            assert result["estado"] == "ACTIVA"
            assert len(h.events("RENOVACION")) == 1
        else:
            with pytest.raises(ErrorAutenticacion):
                auth.confirmar(pending["rotacion_id"], pending["token_renovacion"])
            assert h.events("RENOVACION") == []
        assert len(roots) == 2 and roots[0] is not roots[1]
        assert len(competitors) == 1
    if winner in {"logout", "revocar"}:
        with h.factory() as db:
            assert all(row.estado == "INVALIDADA" for row in db.scalars(select(CredencialSesion)))


@pytest.mark.parametrize("phase", ["upsert", "quota_lock", "admission_flush", "failure_lock", "failure_flush", "success_flush", "unlock_flush"])
def test_c2_cuota_y_finalizacion_reintentadas_sin_doble_reserva_ni_argon(h, monkeypatch, phase):
    pair = h.login() if phase == "unlock_flush" else None
    if pair:
        h.advance(1801)
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now, origen="puesto-local")
        use_driver_policy(auth, monkeypatch)
        password = Mock(wraps=auth._password)
        monkeypatch.setattr(auth, "_password", password)
        injected = []
        def fail_execute(state):
            target = False
            if phase == "upsert":
                target = state.is_insert and state.statement.table.name == "limites_autenticacion"
            if phase in {"quota_lock", "failure_lock"} and is_lock(state, LimiteAutenticacion):
                # Admission locks both buckets; failure finalization locks just the account.
                target = ((" IN (" in str(state.statement)) == (phase == "quota_lock"))
            if target and not injected:
                injected.append(True)
                raise sql_error()
        def fail_flush(session, flush_context, instances):
            events = {row.evento for row in db.new if isinstance(row, AuditoriaAutenticacion)}
            target = (phase == "admission_flush" and not events and any(isinstance(row, LimiteAutenticacion) for row in db.dirty))
            target |= phase == "failure_flush" and "INTENTO_FALLIDO" in events
            target |= phase == "success_flush" and "INICIO" in events
            target |= phase == "unlock_flush" and "DESBLOQUEO" in events
            if target and not injected:
                injected.append(True)
                raise sql_error()
        event.listen(db, "do_orm_execute", fail_execute)
        event.listen(db, "before_flush", fail_flush)
        if phase == "unlock_flush":
            auth.desbloquear(pair["sesion_id"], "cajero.uno", PASSWORD)
        elif phase == "success_flush":
            auth.login("cajero.uno", PASSWORD)
        else:
            with pytest.raises(ErrorAutenticacion):
                auth.login("cajero.uno", WRONG)
        assert injected == [True]
        password.assert_called_once()
        assert db.get_transaction() is None
    with h.factory() as db:
        origin = next(row for row in db.scalars(select(LimiteAutenticacion)) if row.ultimo_fallo_at is None and row.intentos > 0)
        # One reservation for this operation (plus the setup login for unlock).
        assert origin.intentos == 1  # The setup login's origin window elapsed before unlock.
        account = [row for row in db.scalars(select(LimiteAutenticacion)) if row.ultimo_fallo_at is not None]
        if phase not in {"success_flush", "unlock_flush"}:
            assert len(account) == 1 and account[0].intentos == 1
            assert len(h.events("INTENTO_FALLIDO")) == 1
        else:
            assert h.events("INTENTO_FALLIDO") == []
            assert db.scalar(select(func.count()).select_from(SesionUsuario)) == 1
            assert len(h.events("DESBLOQUEO" if pair else "INICIO")) == 1


def test_c2_bloquea_cuotas_antes_de_crear_vista_de_politica(h):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        statements = []
        event.listen(db, "do_orm_execute", lambda state: statements.append(state.statement))
        auth._admit("cajero.uno")
        first_read = next(stmt for stmt in statements if stmt.is_select)
        assert first_read._for_update_arg is not None
        assert first_read.column_descriptions[0]["expr"] is LimiteAutenticacion
        assert all(stmt.is_insert for stmt in statements[:2])


@pytest.mark.parametrize("action", ["confirmar", "preparar", "logout", "revocar", "admit"])
def test_c2_conflicto_persistente_tres_intentos_sin_commit(h, monkeypatch, action):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        if action == "admit":
            operate = lambda: auth._admit("cajero.uno")
        else:
            _, operate = setup_operation(h, auth, action)
        use_driver_policy(auth, monkeypatch)
        calls = []
        failure = sql_error()
        def fail(state):
            target = is_lock(state, LimiteAutenticacion if action == "admit" else SesionUsuario)
            if target:
                calls.append(True)
                raise failure
        event.listen(db, "do_orm_execute", fail)
        commit = Mock(wraps=db.commit)
        rollback = Mock(wraps=db.rollback)
        monkeypatch.setattr(db, "commit", commit)
        monkeypatch.setattr(db, "rollback", rollback)
        with pytest.raises(OperationalError) as error:
            operate()
        assert error.value is failure
        assert len(calls) == rollback.call_count == MAX_AUTH_TRANSACTION_ATTEMPTS
        commit.assert_not_called()
        assert db.get_transaction() is None


@pytest.mark.parametrize("action", ["confirmar", "preparar", "logout", "revocar"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_c2_commit_incierto_nunca_se_repite_incluso_1020(h, monkeypatch, action, after_commit):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        pair, operate = setup_operation(h, auth, action)
        use_driver_policy(auth, monkeypatch)
        real_commit = db.commit
        def uncertain():
            if after_commit:
                real_commit()
            raise sql_error()
        commit = Mock(side_effect=uncertain)
        monkeypatch.setattr(db, "commit", commit)
        with pytest.raises(OperationalError):
            operate()
        commit.assert_called_once()
    expected = {"confirmar": "RENOVACION", "preparar": "RENOVACION_PREPARADA",
                "logout": "CIERRE", "revocar": "REVOCACION"}[action]
    assert len(h.events(expected)) == int(after_commit)
    if not after_commit:
        assert h.session(pair["sesion_id"]).generacion == 1


@pytest.mark.parametrize("code", [1044, 1064, 2006, 2013])
def test_c2_otros_errores_no_se_reintentan(h, monkeypatch, code):
    pair = h.login()
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        use_driver_policy(auth, monkeypatch)
        failure = sql_error(code)
        calls = []
        def fail(state):
            if is_lock(state, SesionUsuario):
                calls.append(True)
                raise failure
        event.listen(db, "do_orm_execute", fail)
        with pytest.raises(OperationalError) as error:
            auth.preparar(pair["token_renovacion"])
        assert error.value is failure and len(calls) == 1


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "sqlite", "postgresql"])
@pytest.mark.parametrize("code", [1020, 1205, 1213, 1064, 1044, 2006, 2013])
def test_c2_clasificacion_estricta_de_conflictos(dialect, code):
    assert _is_retryable_auth_conflict(sql_error(code), dialect) == (
        dialect in {"mysql", "mariadb"} and code in {1020, 1205, 1213})


@pytest.mark.parametrize("error", [
    sql_error("1020"), sql_error(1020, invalidated=True),
    IntegrityError("SQL", {}, DriverOperationalError(1020, "simulado")),
    RuntimeError("1020"), OperationalError("SQL", {}, Exception()),
])
def test_c2_no_reintenta_errores_ambiguos_o_conexion_invalidada(error):
    assert not _is_retryable_auth_conflict(error, "mysql")


def test_c2_reintento_recalcula_expiracion_no_activa_candidato_vencido(h, monkeypatch):
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        use_driver_policy(auth, monkeypatch)
        hits, roots = [], []
        event.listen(db, "after_transaction_create", lambda session, tx: roots.append(tx) if tx.parent is None else None)
        def fail_once(state):
            if is_lock(state, CredencialSesion):
                hits.append(True)
                if len(hits) == 1:
                    raise sql_error()
        event.listen(db, "do_orm_execute", fail_once)
        real_rollback = db.rollback
        def rollback_and_expire():
            real_rollback()
            h.advance(60)
        monkeypatch.setattr(db, "rollback", rollback_and_expire)
        with pytest.raises(ErrorAutenticacion):
            auth.confirmar(pending["rotacion_id"], pending["token_renovacion"])
        assert len(hits) >= 2
        assert len(roots) == 2 and roots[0] is not roots[1]
    assert h.session(pair["sesion_id"]).generacion == 1
    assert h.events("RENOVACION") == []


def test_c2_finalizacion_reintentada_revalida_usuario_sin_repetir_login(h, monkeypatch):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        use_driver_policy(auth, monkeypatch)
        admission, password = Mock(wraps=auth._admit), Mock(wraps=auth._password)
        monkeypatch.setattr(auth, "_admit", admission)
        monkeypatch.setattr(auth, "_password", password)
        hits = []
        def fail_once(state):
            if is_lock(state, Usuario):
                hits.append(True)
                if len(hits) == 1:
                    raise sql_error()
        event.listen(db, "do_orm_execute", fail_once)
        real_rollback = db.rollback
        def rollback_and_disable():
            real_rollback()
            with h.factory.begin() as other:
                other.execute(update(Usuario).where(Usuario.nombre_usuario == "cajero.uno")
                              .values(activo=False, version=2))
        monkeypatch.setattr(db, "rollback", rollback_and_disable)
        with pytest.raises(ErrorAutenticacion):
            auth.login("cajero.uno", PASSWORD)
        admission.assert_called_once()
        password.assert_called_once()
        assert len(hits) == 2
    assert h.events("INICIO") == [] and len(h.events("INTENTO_FALLIDO")) == 1


@pytest.mark.parametrize("table", ["sesiones_usuario", "credenciales_sesion", "limites_autenticacion"])
def test_c2_error_sql_durante_flush_recupera_la_sesion_sqlalchemy(h, monkeypatch, table):
    pair = h.login() if table != "limites_autenticacion" else None
    pending = h.call("preparar", pair["token_renovacion"]) if pair else None
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now, origen="puesto-local")
        use_driver_policy(auth, monkeypatch)
        injected = []
        def fail_sql(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith(f"UPDATE {table} ") and not injected:
                injected.append(True)
                raise sql_error()
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", fail_sql)
        try:
            if pending:
                auth.confirmar(pending["rotacion_id"], pending["token_renovacion"])
            else:
                with pytest.raises(ErrorAutenticacion):
                    auth.login("cajero.uno", WRONG)
        finally:
            event.remove(engine, "before_cursor_execute", fail_sql)
        assert injected == [True]
        assert db.get_transaction() is None and db.is_active
    if pending:
        assert len(h.events("RENOVACION")) == 1
        assert h.session(pair["sesion_id"]).generacion == 2
    else:
        assert len(h.events("INTENTO_FALLIDO")) == 1
        with h.factory() as db:
            assert db.scalar(select(LimiteAutenticacion.intentos).where(
                LimiteAutenticacion.ultimo_fallo_at.is_not(None))) == 1


@pytest.mark.parametrize("unlock", [False, True])
def test_c2_fallo_con_cuenta_conocida_respeta_orden_usuario_cuota_fk(h, unlock):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        reservation = auth._admit("cajero.uno")
        verified = auth._password("cajero.uno", WRONG)
        assert verified is None
        locks = []
        def record(state):
            for model in (Usuario, LimiteAutenticacion):
                if is_lock(state, model):
                    locks.append(model)
        event.listen(db, "do_orm_execute", record)
        with pytest.raises(ErrorAutenticacion):
            if unlock:
                auth._desbloquear_verificado("localizador-sin-privilegios", reservation, verified)
            else:
                auth._login_verificado(reservation, verified)
        assert locks == [Usuario, LimiteAutenticacion]
    assert h.events("INTENTO_FALLIDO")[0].usuario_id is not None
