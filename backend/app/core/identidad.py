import re
import unicodedata


def normalizar_nombre_usuario(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Nombre de usuario invalido")
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", value):
        raise ValueError("El usuario requiere 3 a 64 caracteres: letras ASCII, numeros, punto, guion o guion bajo")
    return value
