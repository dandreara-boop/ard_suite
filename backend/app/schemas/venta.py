from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import EventoPendienteEstado, VentaEstado


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VentaCreate(BaseModel):
    destino_id: int
    usuario_id: int | None = None


class DetalleVentaCreate(BaseModel):
    variante_id: int
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal = Field(gt=0)


class PagoVentaCreate(BaseModel):
    medio_pago: str
    importe: Decimal = Field(gt=0)


class DetalleVentaRead(ORMModel):
    id: int
    venta_id: int
    variante_id: int
    codigo_articulo: str
    codigo_barra: str
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    importe: Decimal
    created_at: datetime


class PagoVentaRead(ORMModel):
    id: int
    venta_id: int
    medio_pago: str
    importe: Decimal
    created_at: datetime


class VentaRead(ORMModel):
    id: int
    global_id: str
    numero_venta: str
    destino_id: int
    estado: VentaEstado
    subtotal: Decimal
    total: Decimal
    usuario_id: int | None
    created_at: datetime
    updated_at: datetime
    cerrada_at: datetime | None
    detalles: list[DetalleVentaRead] = Field(default_factory=list)
    pagos: list[PagoVentaRead] = Field(default_factory=list)


class EventoPendienteRead(ORMModel):
    id: int
    global_id: str
    tipo: str
    aggregate_type: str
    aggregate_id: str
    payload: dict
    estado: EventoPendienteEstado
    intentos: int
    created_at: datetime
    processed_at: datetime | None
    ultimo_error: str | None


class ProcesarEventosRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)


class ProcesarEventosResult(BaseModel):
    procesados: int
    errores: int


def new_event_global_id() -> str:
    return str(uuid4())
