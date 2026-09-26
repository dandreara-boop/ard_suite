from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas.commercial import (
    CotizacionVentaRead,
    MedioPagoCreate,
    MedioPagoRead,
    MedioPagoUpdate,
    ResolucionComercialRead,
    ResolucionComercialRequest,
    ResolucionComercialVentaRead,
)
from backend.app.services.commercial_service import CommercialService
from backend.app.services.exceptions import CatalogError

medios_router = APIRouter(prefix="/api/medios-pago", tags=["medios-pago"])
ventas_comercial_router = APIRouter(prefix="/api/ventas", tags=["comercial"])


@medios_router.post("", response_model=MedioPagoRead, status_code=status.HTTP_201_CREATED)
def create_medio_pago(data: MedioPagoCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return CommercialService(db).create_medio(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@medios_router.get("", response_model=list[MedioPagoRead])
def list_medios_pago(db: Annotated[Session, Depends(get_db)], active_only: bool = False):
    return CommercialService(db).list_medios(active_only=active_only)


@medios_router.get("/{medio_id}", response_model=MedioPagoRead)
def get_medio_pago(medio_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CommercialService(db).get_medio(medio_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@medios_router.put("/{medio_id}", response_model=MedioPagoRead)
def update_medio_pago(medio_id: int, data: MedioPagoUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return CommercialService(db).update_medio(medio_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@ventas_comercial_router.get("/{venta_id}/comercial/cotizacion/condiciones/{condicion_id}", response_model=CotizacionVentaRead)
def cotizar_venta_por_condicion(
    venta_id: int,
    condicion_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CommercialService(db).cotizar_por_condicion(venta_id, condicion_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@ventas_comercial_router.get("/{venta_id}/comercial/cotizacion/medios/{medio_id}", response_model=CotizacionVentaRead)
def cotizar_venta_por_medio(
    venta_id: int,
    medio_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CommercialService(db).cotizar_por_medio(venta_id, medio_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@ventas_comercial_router.post("/{venta_id}/comercial/resolucion/simular", response_model=ResolucionComercialRead)
def simular_resolucion_comercial(
    venta_id: int,
    data: ResolucionComercialRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CommercialService(db).simular_resolucion(venta_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@ventas_comercial_router.post("/{venta_id}/comercial/resolucion/confirmar", response_model=ResolucionComercialVentaRead)
def confirmar_resolucion_comercial(
    venta_id: int,
    data: ResolucionComercialRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CommercialService(db).confirmar_resolucion(venta_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@ventas_comercial_router.get("/{venta_id}/comercial/resolucion", response_model=ResolucionComercialVentaRead)
def get_resolucion_comercial(venta_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CommercialService(db).get_resolucion(venta_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error
