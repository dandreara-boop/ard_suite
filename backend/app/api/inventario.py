from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.models import MovimientoStockTipo, StockEstado
from backend.app.schemas import (
    AjusteStockCreate,
    DestinoInventarioCreate,
    DestinoInventarioRead,
    InventoryMovementResultRead,
    MovimientoStockCreate,
    MovimientoStockRead,
    StockActualRead,
)
from backend.app.services import InventoryService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/inventario", tags=["inventario"])


@router.post("/destinos", response_model=DestinoInventarioRead, status_code=201)
def create_destino(data: DestinoInventarioCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return InventoryService(db).create_destino(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/destinos", response_model=list[DestinoInventarioRead])
def list_destinos(db: Annotated[Session, Depends(get_db)]):
    return InventoryService(db).list_destinos()


@router.post("/movimientos", response_model=InventoryMovementResultRead, status_code=201)
def create_movimiento(data: MovimientoStockCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return InventoryService(db).registrar_movimiento(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/stock", response_model=list[StockActualRead])
def list_stock(
    db: Annotated[Session, Depends(get_db)],
    variante_id: int | None = None,
    destino_id: int | None = None,
    estado: StockEstado | None = None,
):
    return InventoryService(db).list_stock(
        variante_id=variante_id,
        destino_id=destino_id,
        estado=estado,
    )


@router.get("/stock/{variante_id}", response_model=list[StockActualRead])
def list_stock_by_variante(
    variante_id: int,
    db: Annotated[Session, Depends(get_db)],
    destino_id: int | None = None,
    estado: StockEstado | None = None,
):
    return InventoryService(db).list_stock(
        variante_id=variante_id,
        destino_id=destino_id,
        estado=estado,
    )


@router.get("/movimientos", response_model=list[MovimientoStockRead])
def list_movimientos(
    db: Annotated[Session, Depends(get_db)],
    variante_id: int | None = None,
    destino_id: int | None = None,
    tipo: MovimientoStockTipo | None = None,
    fecha_desde: datetime | None = None,
    fecha_hasta: datetime | None = None,
):
    return InventoryService(db).list_movimientos(
        variante_id=variante_id,
        destino_id=destino_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )


@router.post("/ajustes", response_model=InventoryMovementResultRead, status_code=201)
def create_ajuste(data: AjusteStockCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return InventoryService(db).ajustar_stock(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error
