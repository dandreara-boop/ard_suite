from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import MovimientoCajaTipo, SesionCajaEstado


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AbrirSesionCajaRequest(BaseModel):
    usuario_id: int
    efectivo_inicial: Decimal = Field(ge=0)


class MovimientoCajaCreate(BaseModel):
    usuario_id: int
    tipo: MovimientoCajaTipo
    importe: Decimal = Field(gt=0)
    motivo: str | None = None


class CerrarSesionCajaRequest(BaseModel):
    usuario_id: int
    efectivo_final_declarado: Decimal = Field(ge=0)
    observacion_cierre: str | None = None


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


class MovimientoCajaRead(ORMModel):
    id: int
    global_id: str
    sesion_caja_id: int
    tipo: MovimientoCajaTipo
    importe: Decimal
    motivo: str | None
    usuario_id: int | None
    fecha: datetime
    referencia: str | None


class CerrarSesionCajaResponse(BaseModel):
    sesion_caja_id: int
    estado: SesionCajaEstado
    arqueo_id: int
    efectivo_esperado: Decimal
    primer_conteo: Decimal
    diferencia: Decimal
    cerrada_at: datetime
    arqueo_fecha: datetime


class SesionCajaResumenRead(BaseModel):
    sesion_id: int
    caja_id: int
    caja_codigo: str
    caja_nombre: str


class AbrirSesionCajaResponse(BaseModel):
    sesion: SesionCajaRead
    advertencia_multiples_sesiones: bool
    otras_sesiones_abiertas: list[SesionCajaResumenRead] = Field(default_factory=list)
