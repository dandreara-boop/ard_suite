from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.schemas.caja import (
    AbrirSesionCajaRequest,
    AbrirSesionCajaResponse,
    CerrarSesionCajaRequest,
    CerrarSesionCajaResponse,
    MovimientoCajaCreate,
    MovimientoCajaRead,
    SesionCajaRead,
    SolicitarCorreccionArqueoRequest,
    SolicitarCorreccionArqueoResponse,
    ResolverSolicitudCorreccionRequest,
    ResolverSolicitudCorreccionResponse,
)
from backend.app.services.caja_service import CajaService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/cajas", tags=["cajas"])


def require_resolucion_desarrollo() -> None:
    # This switch is NOT authentication or supervisor authorization.
    if (
        not settings.caja_correcciones_resolucion_dev_enabled
        or settings.app_env not in {"development", "test", "testing"}
    ):
        raise HTTPException(status_code=404, detail="Endpoint de resolucion deshabilitado")


@router.post(
    "/solicitudes-correccion/{solicitud_id}/resolver",
    response_model=ResolverSolicitudCorreccionResponse,
    dependencies=[Depends(require_resolucion_desarrollo)],
)
def resolver_solicitud_correccion(
    solicitud_id: int,
    data: ResolverSolicitudCorreccionRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CajaService(db).resolver_solicitud_correccion(solicitud_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post(
    "/arqueos/{arqueo_id}/solicitar-correccion",
    response_model=SolicitarCorreccionArqueoResponse,
    status_code=status.HTTP_201_CREATED,
)
def solicitar_correccion_arqueo(
    arqueo_id: int,
    data: SolicitarCorreccionArqueoRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CajaService(db).solicitar_correccion_arqueo(arqueo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{caja_id}/sesiones", response_model=AbrirSesionCajaResponse, status_code=status.HTTP_201_CREATED)
def abrir_sesion_caja(
    caja_id: int,
    data: AbrirSesionCajaRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CajaService(db).abrir_sesion(
            caja_id,
            cajero_id=data.usuario_id,
            efectivo_inicial=data.efectivo_inicial,
        )
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/sesiones/abiertas", response_model=list[SesionCajaRead])
def listar_sesiones_abiertas(usuario_id: int, db: Annotated[Session, Depends(get_db)]):
    return CajaService(db).listar_abiertas_por_cajero(usuario_id)


@router.post(
    "/sesiones/{sesion_caja_id}/movimientos",
    response_model=MovimientoCajaRead,
    status_code=status.HTTP_201_CREATED,
)
def registrar_movimiento_caja(
    sesion_caja_id: int,
    data: MovimientoCajaCreate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CajaService(db).registrar_movimiento(sesion_caja_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/sesiones/{sesion_caja_id}/movimientos", response_model=list[MovimientoCajaRead])
def listar_movimientos_caja(sesion_caja_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CajaService(db).listar_movimientos(sesion_caja_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/sesiones/{sesion_caja_id}/cerrar", response_model=CerrarSesionCajaResponse)
def cerrar_sesion_caja(
    sesion_caja_id: int,
    data: CerrarSesionCajaRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return CajaService(db).cerrar_sesion(sesion_caja_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error
