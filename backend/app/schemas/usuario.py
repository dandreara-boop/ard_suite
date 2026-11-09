from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from backend.app.core.passwords import validar_password
from backend.app.core.identidad import normalizar_nombre_usuario
from backend.app.models.usuario import RolUsuario
from backend.app.services.exceptions import ValidationError


class UsuarioCreate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    nombre_usuario: str
    nombre_visible: str = Field(min_length=1, max_length=120)
    password: SecretStr = Field(repr=False, exclude=True)
    rol: RolUsuario = RolUsuario.CAJERO

    @field_validator("nombre_usuario")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return normalizar_nombre_usuario(value)

    @field_validator("nombre_visible")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre visible es obligatorio")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        try:
            validar_password(value.get_secret_value())
        except ValidationError as error:
            raise ValueError("La contrasena debe tener entre 15 y 1024 caracteres y no ser solo espacios") from error
        return value


class UsuarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    global_id: str
    nombre_usuario: str
    nombre_visible: str
    activo: bool
    rol: RolUsuario
    version: int
    created_at: datetime
    updated_at: datetime
