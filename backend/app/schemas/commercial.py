from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TipoSolicitudPago(str, Enum):
    IMPORTE_FIJO = "IMPORTE_FIJO"
    RESTO = "RESTO"


class MedioPagoBase(BaseModel):
    codigo: str
    nombre: str
    condicion_comercial_id: int
    activo: bool = True


class MedioPagoCreate(MedioPagoBase):
    pass


class MedioPagoUpdate(BaseModel):
    codigo: str | None = None
    nombre: str | None = None
    condicion_comercial_id: int | None = None
    activo: bool | None = None


class MedioPagoRead(MedioPagoBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class CotizacionLineaRead(BaseModel):
    detalle_id: int
    articulo_id: int
    variante_id: int
    codigo_articulo: str
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    subtotal: Decimal


class CotizacionVentaRead(BaseModel):
    venta_id: int
    condicion_comercial_id: int
    medio_pago_id: int | None = None
    lineas: list[CotizacionLineaRead]
    total: Decimal


class PagoResolucionRequest(BaseModel):
    medio_pago_id: int
    tipo: TipoSolicitudPago = TipoSolicitudPago.IMPORTE_FIJO
    importe: Decimal | None = None


class ResolucionComercialRequest(BaseModel):
    pagos: list[PagoResolucionRequest] = Field(min_length=1)
    usuario_id: int | None = None


class AsignacionComercialRead(BaseModel):
    medio_pago_id: int
    medio_pago_codigo: str
    condicion_comercial_id: int
    detalle_id: int
    articulo_id: int
    variante_id: int
    cantidad: Decimal
    fraccion: Decimal
    precio_origen: Decimal
    precio_destino: Decimal
    importe_origen: Decimal
    importe_calculado: Decimal
    importe_final: Decimal
    unidad_indice: int
    es_fraccion: bool


class ResultadoMedioPagoRead(BaseModel):
    medio_pago_id: int
    medio_pago_codigo: str
    condicion_comercial_id: int
    tipo_solicitud: TipoSolicitudPago
    importe_solicitado: Decimal | None
    importe_final: Decimal


class ResolucionComercialRead(BaseModel):
    venta_id: int
    total: Decimal
    pagos: list[ResultadoMedioPagoRead]
    asignaciones: list[AsignacionComercialRead]
    traza: dict


class ResolucionComercialVentaRead(ORMModel):
    id: int
    venta_id: int
    total: Decimal
    incremento_redondeo: Decimal
    solicitud: dict
    resultado: dict
    traza: dict
    created_at: datetime
    updated_at: datetime
