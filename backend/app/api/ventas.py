from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.models import EventoPendienteEstado
from backend.app.schemas import (
    CapturarVentaRequest,
    DetalleVentaCreate,
    EventoPendienteRead,
    PagoVentaCreate,
    ProcesarEventosRequest,
    ProcesarEventosResult,
    VentaCreate,
    VentaOperacionRequest,
    VentaRead,
    VentaUpdate,
)
from backend.app.services.exceptions import CatalogError
from backend.app.services.venta_service import VentaService

router = APIRouter(prefix="/api/ventas", tags=["ventas"])


@router.post("", response_model=VentaRead, status_code=status.HTTP_201_CREATED)
def create_venta(data: VentaCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("", response_model=list[VentaRead])
def list_ventas(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return VentaService(db).list(skip, limit)


@router.get("/caja/pendientes", response_model=list[VentaRead])
def list_ventas_pendientes_caja(
    db: Annotated[Session, Depends(get_db)],
    destino_id: int,
    numero_corto: int | None = None,
    referencia_cliente: str | None = None,
):
    return VentaService(db).listar_pendientes_caja(
        destino_id=destino_id,
        numero_corto=numero_corto,
        referencia_cliente=referencia_cliente,
    )


@router.get("/eventos/pendientes", response_model=list[EventoPendienteRead])
def list_eventos_pendientes(
    db: Annotated[Session, Depends(get_db)],
    estado: EventoPendienteEstado | None = None,
    tipo: str | None = None,
):
    return VentaService(db).list_eventos(estado=estado, tipo=tipo)


@router.post("/eventos/procesar", response_model=ProcesarEventosResult)
def procesar_eventos(data: ProcesarEventosRequest, db: Annotated[Session, Depends(get_db)]):
    result = VentaService(db).procesar_eventos_pendientes(data.limit)
    return ProcesarEventosResult(procesados=result.procesados, errores=result.errores)


@router.get("/{venta_id}", response_model=VentaRead)
def get_venta(venta_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).get(venta_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{venta_id}", response_model=VentaRead)
def update_venta(venta_id: int, data: VentaUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).update_preparacion(venta_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/items", response_model=VentaRead, status_code=status.HTTP_201_CREATED)
def add_item(venta_id: int, data: DetalleVentaCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).add_item(venta_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.delete("/{venta_id}/items/{item_id}", response_model=VentaRead)
def remove_item(venta_id: int, item_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).remove_item(venta_id, item_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/pagos", response_model=VentaRead, status_code=status.HTTP_201_CREATED)
def add_pago(venta_id: int, data: PagoVentaCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).add_pago(venta_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.delete("/{venta_id}/pagos/{pago_id}", response_model=VentaRead)
def remove_pago(venta_id: int, pago_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).remove_pago(venta_id, pago_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/finalizar", response_model=VentaRead)
def finalizar_venta(venta_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).finalizar(venta_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/enviar-a-caja", response_model=VentaRead)
def enviar_venta_a_caja(
    venta_id: int,
    db: Annotated[Session, Depends(get_db)],
    data: VentaOperacionRequest | None = None,
):
    try:
        return VentaService(db).enviar_a_caja(venta_id, usuario_id=data.usuario_id if data else None)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/capturar", response_model=VentaRead)
def capturar_venta(venta_id: int, data: CapturarVentaRequest, db: Annotated[Session, Depends(get_db)]):
    try:
        return VentaService(db).capturar(venta_id, caja_id=data.caja_id, usuario_id=data.usuario_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/liberar", response_model=VentaRead)
def liberar_venta(
    venta_id: int,
    db: Annotated[Session, Depends(get_db)],
    data: VentaOperacionRequest | None = None,
):
    try:
        return VentaService(db).liberar(venta_id, usuario_id=data.usuario_id if data else None)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{venta_id}/anular", response_model=VentaRead)
def anular_venta(
    venta_id: int,
    db: Annotated[Session, Depends(get_db)],
    data: VentaOperacionRequest | None = None,
):
    try:
        return VentaService(db).anular(venta_id, usuario_id=data.usuario_id if data else None)
    except CatalogError as error:
        raise map_catalog_error(error) from error
