from functools import lru_cache
from secrets import token_urlsafe
import re

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.passwords import generar_hash_password, verificar_password
from backend.app.models.usuario import InicializacionIdentidad, RolUsuario, Usuario
from backend.app.schemas.usuario import UsuarioCreate, UsuarioRead, normalizar_nombre_usuario
from backend.app.services.exceptions import ConflictError, ValidationError


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return generar_hash_password(token_urlsafe(32))


class UsuarioService:
    """Internal identity primitives, not an authenticated user-administration API."""
    def __init__(self, db: Session) -> None:
        self.db = db

    def crear_usuario(self, data: UsuarioCreate) -> UsuarioRead:
        return self._crear(data, primer_administrador=False)

    def crear_primer_administrador(self, data: UsuarioCreate) -> UsuarioRead:
        return self._crear(data, primer_administrador=True)

    def _crear(self, data: UsuarioCreate, *, primer_administrador: bool) -> UsuarioRead:
        try:
            # Revalidate even if an internal caller used model_construct().
            data = UsuarioCreate(
                nombre_usuario=data.nombre_usuario, nombre_visible=data.nombre_visible,
                password=data.password, rol=data.rol,
            )
            if primer_administrador and data.rol != RolUsuario.ADMINISTRADOR:
                raise ValidationError("La inicializacion requiere el rol ADMINISTRADOR")
            if not primer_administrador and data.rol == RolUsuario.ADMINISTRADOR:
                raise ValidationError("La creacion de administradores solo esta disponible mediante inicializacion local")
            # Argon2 runs before holding the initialization lock.
            password_hash = generar_hash_password(data.password.get_secret_value())
            dialect = self.db.get_bind().dialect.name
            if dialect == "sqlite":
                self.db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if primer_administrador:
                # The singleton PK is the atomic, durable bootstrap reservation.
                # Concurrent installers cannot both insert it, even under RR.
                self.db.add(InicializacionIdentidad(id=1))
                try:
                    self.db.flush()
                except IntegrityError as error:
                    self.db.rollback()
                    if _is_bootstrap_duplicate(error) and self.db.get(InicializacionIdentidad, 1) is not None:
                        raise ConflictError("Las identidades ya fueron inicializadas") from None
                    raise
                if self.db.scalar(select(Usuario.id).limit(1).with_for_update()) is not None:
                    raise ConflictError("Ya existen identidades; no se permite una segunda inicializacion")
            else:
                # All identity creation reserves the same marker. It survives
                # account deletion and never changes its original timestamp.
                if dialect == "sqlite":
                    statement = sqlite_insert(InicializacionIdentidad).values(id=1).on_conflict_do_nothing(index_elements=["id"])
                elif dialect in {"mysql", "mariadb"}:
                    statement = mysql_insert(InicializacionIdentidad).values(id=1).on_duplicate_key_update(id=1)
                else:
                    raise ValidationError("Motor de identidad no soportado")
                self.db.execute(statement)
            if self.db.scalar(select(Usuario.id).where(Usuario.nombre_usuario == data.nombre_usuario).with_for_update()) is not None:
                raise ConflictError("El nombre de usuario ya existe")
            usuario = Usuario(
                nombre_usuario=data.nombre_usuario, nombre_visible=data.nombre_visible,
                password_hash=password_hash, rol=data.rol,
            )
            self.db.add(usuario)
            self.db.flush()
            result = UsuarioRead.model_validate(usuario)
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def verificar_credenciales(self, nombre_usuario: str, password: str) -> UsuarioRead | None:
        """Read-only credential check; creates no session, token, cookie or login."""
        try:
            nombre_usuario = normalizar_nombre_usuario(nombre_usuario)
        except ValueError:
            verificar_password(password, _dummy_hash())
            return None
        usuario = self.db.scalar(
            select(Usuario).where(Usuario.nombre_usuario == nombre_usuario)
            .execution_options(populate_existing=True)
        )
        if usuario is None or not usuario.activo:
            verificar_password(password, _dummy_hash())
            return None
        if not verificar_password(password, usuario.password_hash):
            return None
        return UsuarioRead.model_validate(usuario)


def _is_bootstrap_duplicate(error: IntegrityError) -> bool:
    if not isinstance(error.statement, str) or "INSERT INTO inicializacion_identidad" not in error.statement:
        return False
    original = error.orig
    if (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_PRIMARYKEY"
        and str(original) == "UNIQUE constraint failed: inicializacion_identidad.id"
    ):
        return True
    args = getattr(original, "args", ())
    if args and args[0] == 1062:
        key = re.search(r"for key ['`]([^'`]+)['`]", str(original), re.IGNORECASE)
        return bool(key and key.group(1).split(".")[-1] == "PRIMARY")
    return False
