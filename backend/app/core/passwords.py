"""Password primitives. Never log inputs, encoded hashes or library exceptions."""
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from backend.app.services.exceptions import ValidationError

_HASHER = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=65536, parallelism=4)


def validar_password(password: str) -> None:
    if not isinstance(password, str) or not 15 <= len(password) <= 1024 or password.isspace():
        raise ValidationError("La contrasena debe tener entre 15 y 1024 caracteres y no ser solo espacios")


def generar_hash_password(password: str) -> str:
    validar_password(password)
    return _HASHER.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    if (
        not isinstance(password, str) or not 15 <= len(password) <= 1024
        or not isinstance(password_hash, str) or not password_hash.startswith("$argon2id$")
    ):
        return False
    try:
        return _HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
