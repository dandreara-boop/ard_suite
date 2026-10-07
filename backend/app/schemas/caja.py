from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import SesionCajaEstado


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AbrirSesionCajaRequest(BaseModel):
    usuario_id: int
    efectivo_inicial: Decimal = Field(ge=0)


class CajaResumenRead(ORMModel):
    id: int
    codigo: str
    nombre: str
    destino_id: int
    activa: bool


class SesionCajaRead(ORMModel):
    id: int
    global_id: str
    caja_id: int
    cajero_id: int
    abierta_at: datetime
    cerrada_at: datetime | None
    efectivo_inicial: Decimal
    estado: SesionCajaEstado
    caja: CajaResumenRead


class SesionCajaResumenRead(BaseModel):
    sesion_id: int
    caja_id: int
    caja_codigo: str
    caja_nombre: str


class AbrirSesionCajaResponse(BaseModel):
    sesion: SesionCajaRead
    advertencia_multiples_sesiones: bool
    otras_sesiones_abiertas: list[SesionCajaResumenRead] = Field(default_factory=list)
