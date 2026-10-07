from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import EventoPendienteEstado, VentaEstado, VentaTipoAtencion


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VentaCreate(BaseModel):
    destino_id: int
    usuario_id: int | None = None
    referencia_cliente: str | None = None
    vendedor_id: int | None = None
    tipo_atencion: VentaTipoAtencion = VentaTipoAtencion.ATENDIDA


class VentaUpdate(BaseModel):
    referencia_cliente: str | None = None
    vendedor_id: int | None = None
    tipo_atencion: VentaTipoAtencion | None = None


class VentaOperacionRequest(BaseModel):
    usuario_id: int | None = None


class CapturarVentaRequest(BaseModel):
    sesion_caja_id: int
    usuario_id: int


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
    medio_pago_id: int | None
    medio_pago: str
    importe: Decimal
    created_at: datetime


class VentaRead(ORMModel):
    id: int
    global_id: str
    numero_venta: str
    numero_corto: int | None
    referencia_cliente: str | None
    destino_id: int
    estado: VentaEstado
    subtotal: Decimal
    total: Decimal
    usuario_id: int | None
    vendedor_id: int | None
    tipo_atencion: VentaTipoAtencion
    caja_captura_id: int | None
    sesion_caja_id: int | None
    capturada_at: datetime | None
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
