from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.pricing import (
    CondicionPrecioTipo,
    MotivoAuditoriaPrecio,
    OrigenPrecioArticulo,
    TipoRedondeoPrecio,
    TipoReglaPrecio,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CondicionComercialPrecioBase(BaseModel):
    codigo: str
    nombre: str
    tipo: CondicionPrecioTipo
    condicion_base_id: int | None = None
    tipo_regla: TipoReglaPrecio | None = None
    porcentaje: Decimal | None = None
    tipo_redondeo: TipoRedondeoPrecio = TipoRedondeoPrecio.SIN_REDONDEO
    multiplo_redondeo: Decimal | None = None
    activa: bool = True
    orden: int = 0


class CondicionComercialPrecioCreate(CondicionComercialPrecioBase):
    pass


class CondicionComercialPrecioUpdate(BaseModel):
    codigo: str | None = None
    nombre: str | None = None
    tipo: CondicionPrecioTipo | None = None
    condicion_base_id: int | None = None
    tipo_regla: TipoReglaPrecio | None = None
    porcentaje: Decimal | None = None
    tipo_redondeo: TipoRedondeoPrecio | None = None
    multiplo_redondeo: Decimal | None = None
    activa: bool | None = None
    orden: int | None = None


class CondicionComercialPrecioRead(CondicionComercialPrecioBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class PrecioBaseArticuloRequest(BaseModel):
    precio: Decimal = Field(gt=Decimal("0"))
    condicion_comercial_id: int | None = None
    usuario_id: int | None = None


class PrecioManualArticuloRequest(BaseModel):
    condicion_comercial_id: int
    precio: Decimal = Field(gt=Decimal("0"))
    usuario_id: int | None = None


class PrecioArticuloRead(ORMModel):
    id: int
    articulo_id: int
    condicion_comercial_id: int
    precio: Decimal
    origen: OrigenPrecioArticulo
    created_at: datetime
    updated_at: datetime


class PrecioVigenteRead(BaseModel):
    articulo_id: int
    condicion_comercial_id: int
    precio: Decimal
    origen: OrigenPrecioArticulo


class RecalculoReglaPreviewRequest(BaseModel):
    tipo_regla: TipoReglaPrecio
    porcentaje: Decimal
    tipo_redondeo: TipoRedondeoPrecio
    multiplo_redondeo: Decimal | None = None


class RecalculoReglaItemRead(BaseModel):
    articulo_id: int
    articulo_codigo: str
    articulo_nombre: str
    condicion_comercial_id: int
    precio_actual: Decimal
    precio_propuesto: Decimal
    origen_actual: OrigenPrecioArticulo


class RecalculoReglaPreviewRead(BaseModel):
    articulos_afectados: int
    precios_regla: int
    precios_manual: int
    items: list[RecalculoReglaItemRead]


class PoliticaRecalculoManual(str, Enum):
    CONSERVAR_MANUALES = "CONSERVAR_MANUALES"
    APLICAR_REGLA_A_TODOS = "APLICAR_REGLA_A_TODOS"
    REVISAR_EXCEPCIONES = "REVISAR_EXCEPCIONES"


class AplicarRecalculoReglaRequest(RecalculoReglaPreviewRequest):
    politica_manuales: str
    confirmar: bool = False
    usuario_id: int | None = None

    @field_validator("politica_manuales")
    @classmethod
    def validate_policy(cls, value: str) -> str:
        allowed = {
            PoliticaRecalculoManual.CONSERVAR_MANUALES,
            PoliticaRecalculoManual.APLICAR_REGLA_A_TODOS,
            PoliticaRecalculoManual.REVISAR_EXCEPCIONES,
        }
        if value not in allowed:
            raise ValueError("Politica de manuales invalida")
        return value


class AplicarRecalculoReglaRead(BaseModel):
    condicion_comercial_id: int
    articulos_afectados: int
    precios_actualizados: int
    precios_manual_conservados: int


class AuditoriaPrecioArticuloRead(ORMModel):
    id: int
    articulo_id: int
    condicion_comercial_id: int
    precio_anterior: Decimal | None
    precio_nuevo: Decimal
    origen_anterior: OrigenPrecioArticulo | None
    origen_nuevo: OrigenPrecioArticulo
    motivo: MotivoAuditoriaPrecio
    usuario_id: int | None
    created_at: datetime
