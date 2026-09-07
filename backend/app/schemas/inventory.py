from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import DestinoInventarioTipo, MovimientoStockTipo, StockEstado


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InventoryRuleResultRead(BaseModel):
    code: str
    message: str
    status: str
    data: dict = Field(default_factory=dict)


class DestinoInventarioBase(BaseModel):
    codigo: str
    nombre: str
    tipo: DestinoInventarioTipo
    activo: bool = True


class DestinoInventarioCreate(DestinoInventarioBase):
    pass


class DestinoInventarioRead(DestinoInventarioBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class StockActualRead(ORMModel):
    id: int
    variante_id: int
    destino_id: int
    estado: StockEstado
    cantidad: int
    updated_at: datetime


class MovimientoStockCreate(BaseModel):
    global_id: str = Field(default_factory=lambda: str(uuid4()))
    variante_id: int
    destino_id: int
    estado: StockEstado = StockEstado.DISPONIBLE
    cantidad: int
    tipo: MovimientoStockTipo
    fecha: datetime | None = None
    usuario_id: int | None = None
    origen_tipo: str
    origen_id: str | None = None
    referencia: str | None = None
    motivo: str | None = None
    observacion: str | None = None


class MovimientoStockRead(ORMModel):
    id: int
    global_id: str
    variante_id: int
    destino_id: int
    estado: StockEstado
    cantidad: int
    tipo: MovimientoStockTipo
    fecha: datetime
    usuario_id: int | None
    origen_tipo: str
    origen_id: str | None
    referencia: str | None
    motivo: str | None
    observacion: str | None
    saldo_anterior: int | None
    saldo_resultante: int | None
    created_at: datetime


class InventoryMovementResultRead(BaseModel):
    applied: bool
    rule_result: InventoryRuleResultRead
    movimiento: MovimientoStockRead
    stock: StockActualRead | None = None


class AjusteStockCreate(BaseModel):
    global_id: str = Field(default_factory=lambda: str(uuid4()))
    variante_id: int
    destino_id: int
    estado: StockEstado = StockEstado.DISPONIBLE
    stock_teorico: int
    stock_contado: int
    motivo: str
    usuario_id: int | None = None
    observacion: str | None = None
    referencia: str | None = None


class StockRebuildResult(BaseModel):
    rebuilt_rows: int
