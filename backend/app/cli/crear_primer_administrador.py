"""Run from an elevated local terminal: python -m ...crear_primer_administrador."""
from contextlib import contextmanager
import ctypes
import getpass
import os
import sys
import warnings

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from backend.app.core.config import settings
from backend.app.models.usuario import RolUsuario
from backend.app.schemas.usuario import UsuarioCreate
from backend.app.services.usuario_service import UsuarioService


def _is_local_administrator() -> bool:
    try:
        if os.name == "nt":
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except (AttributeError, OSError):
        return False


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


@contextmanager
def _installation_session():
    engine = create_engine(settings.sqlalchemy_database_uri, poolclass=NullPool, hide_parameters=True, echo=False)
    try:
        with Session(engine, expire_on_commit=False, autoflush=False) as db:
            yield db
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print("Uso: python -m backend.app.cli.crear_primer_administrador")
        print("Sin argumentos. Requiere terminal administrativa local y esquema migrado.")
        return 0
    if argv:
        print("Este comando no admite argumentos ni credenciales en la linea de comandos.", file=sys.stderr)
        return 2
    if not _is_local_administrator() or not _is_interactive():
        print("Se requiere una terminal interactiva con privilegios administrativos locales.", file=sys.stderr)
        return 1
    try:
        nombre = input("Nombre de usuario (3-64 caracteres ASCII): ")
        visible = input("Nombre visible: ")
        # Never accept getpass's fallback to visible stdin.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Contrasena (15-1024 caracteres): ")
            confirmacion = getpass.getpass("Repita la contrasena: ")
        if password != confirmacion:
            print("Las contrasenas no coinciden.", file=sys.stderr)
            return 1
        data = UsuarioCreate(nombre_usuario=nombre, nombre_visible=visible, password=password, rol=RolUsuario.ADMINISTRADOR)
        with _installation_session() as db:
            UsuarioService(db).crear_primer_administrador(data)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("Inicializacion cancelada; no se acepta entrada de contrasena visible.", file=sys.stderr)
        return 1
    except Exception:
        # Do not print validation inputs, SQL parameters, hashes or connection URLs.
        print("No se pudo inicializar: revise los datos, el esquema, los permisos y si ya existen identidades.", file=sys.stderr)
        return 1
    print("Inicializacion de identidad completada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
