from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas.caja import AbrirSesionCajaRequest, AbrirSesionCajaResponse, SesionCajaRead
from backend.app.services.caja_service import CajaService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/cajas", tags=["cajas"])


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
