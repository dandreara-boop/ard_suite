"""Server-owned policy. It does not authenticate client-supplied roles or IDs."""
from enum import Enum
from types import MappingProxyType

from backend.app.models.usuario import RolUsuario, Usuario


class Permiso(str, Enum):
    USUARIOS_ADMINISTRAR = "usuarios.administrar"
    CAJA_OPERAR = "caja.operar"
    CORRECCIONES_RESOLVER = "caja.correcciones.resolver"


PERMISOS_POR_ROL = MappingProxyType({
    RolUsuario.ADMINISTRADOR: frozenset({Permiso.USUARIOS_ADMINISTRAR}),
    RolUsuario.SUPERVISOR: frozenset({Permiso.CORRECCIONES_RESOLVER}),
    RolUsuario.CAJERO: frozenset({Permiso.CAJA_OPERAR}),
})


def tiene_permiso(usuario: Usuario, permiso: Permiso) -> bool:
    # Only pass an identity loaded by trusted backend code. No HTTP integration
    # exists in C1; a body containing {rol: ...} must never grant a permission.
    return bool(
        isinstance(usuario, Usuario) and usuario.activo
        and permiso in PERMISOS_POR_ROL.get(usuario.rol, frozenset())
    )
