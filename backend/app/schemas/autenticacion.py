from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class EntradaAutenticacion(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    def __repr_args__(self):
        return iter(())


class LoginRequest(EntradaAutenticacion):
    nombre_usuario: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


class DesbloquearRequest(LoginRequest):
    sesion_id: UUID


class RenovarRequest(EntradaAutenticacion):
    token_renovacion: SecretStr = Field(min_length=40, max_length=128)


class ConfirmarRequest(RenovarRequest):
    rotacion_id: UUID


class RespuestaAutenticacion(BaseModel):
    def __repr_args__(self):
        return iter(())


class CredencialesResponse(RespuestaAutenticacion):
    sesion_id: str
    rotacion_id: str
    token_acceso: str
    token_renovacion: str
    estado: str
    confirmar_hasta: datetime | None
    acceso_hasta: datetime | None
    renovacion_hasta: datetime | None


class ConfirmacionResponse(RespuestaAutenticacion):
    estado: str
    acceso_hasta: datetime
    renovacion_hasta: datetime


class SesionResponse(RespuestaAutenticacion):
    sesion_id: str
    usuario_id: int
    estado: str
    creada_at: datetime
    actividad_at: datetime
    bloqueada_at: datetime | None
