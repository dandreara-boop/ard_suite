from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas.pricing import (
    AplicarRecalculoReglaRead,
    AplicarRecalculoReglaRequest,
    CondicionComercialPrecioCreate,
    CondicionComercialPrecioRead,
    CondicionComercialPrecioUpdate,
    PrecioArticuloRead,
    PrecioBaseArticuloRequest,
    PrecioManualArticuloRequest,
    PrecioVigenteRead,
    RecalculoReglaPreviewRead,
    RecalculoReglaPreviewRequest,
)
from backend.app.services.exceptions import CatalogError
from backend.app.services.pricing_service import PricingService

router = APIRouter(prefix="/api/precios", tags=["precios"])


@router.post("/condiciones", response_model=CondicionComercialPrecioRead, status_code=status.HTTP_201_CREATED)
def create_condicion(data: CondicionComercialPrecioCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return PricingService(db).create_condicion(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/condiciones", response_model=list[CondicionComercialPrecioRead])
def list_condiciones(db: Annotated[Session, Depends(get_db)], active_only: bool = False):
    return PricingService(db).list_condiciones(active_only=active_only)


@router.get("/condiciones/{condicion_id}", response_model=CondicionComercialPrecioRead)
def get_condicion(condicion_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return PricingService(db).get_condicion(condicion_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/condiciones/{condicion_id}", response_model=CondicionComercialPrecioRead)
def update_condicion(
    condicion_id: int,
    data: CondicionComercialPrecioUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return PricingService(db).update_condicion(condicion_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/articulos/{articulo_id}", response_model=list[PrecioArticuloRead])
def list_precios_articulo(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return PricingService(db).list_precios_articulo(articulo_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/articulos/{articulo_id}/base", response_model=list[PrecioArticuloRead])
def set_precio_base(
    articulo_id: int,
    data: PrecioBaseArticuloRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return PricingService(db).set_precio_base(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/articulos/{articulo_id}/manual", response_model=PrecioArticuloRead)
def set_precio_manual(
    articulo_id: int,
    data: PrecioManualArticuloRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return PricingService(db).set_precio_manual(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/articulos/{articulo_id}/condiciones/{condicion_id}", response_model=PrecioVigenteRead)
def get_precio_vigente(articulo_id: int, condicion_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        price = PricingService(db).get_precio_vigente(articulo_id, condicion_id)
        return PrecioVigenteRead(
            articulo_id=price.articulo_id,
            condicion_comercial_id=price.condicion_comercial_id,
            precio=price.precio,
            origen=price.origen,
        )
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/condiciones/{condicion_id}/recalculo/preview", response_model=RecalculoReglaPreviewRead)
def preview_recalculo_regla(
    condicion_id: int,
    data: RecalculoReglaPreviewRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return PricingService(db).preview_recalculo_regla(condicion_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/condiciones/{condicion_id}/recalculo/aplicar", response_model=AplicarRecalculoReglaRead)
def aplicar_recalculo_regla(
    condicion_id: int,
    data: AplicarRecalculoReglaRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return PricingService(db).aplicar_recalculo_regla(condicion_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error
