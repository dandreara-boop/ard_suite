from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import (
    VarianteCreate,
    VarianteDetailRead,
    VarianteRead,
    VarianteUpdate,
    VarianteValorAtributoRead,
)
from backend.app.services import VarianteService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/variantes", tags=["variantes"])


@router.get("", response_model=list[VarianteRead])
def list_variantes(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return VarianteService(db).list(skip, limit)


@router.get("/barcode/{codigo_barra}", response_model=VarianteDetailRead)
def get_by_barcode(codigo_barra: str, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).get_by_codigo_barra(codigo_barra)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/{variante_id}", response_model=VarianteDetailRead)
def get_variante(variante_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).get(variante_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=VarianteDetailRead, status_code=201)
def create_variante(data: VarianteCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{variante_id}", response_model=VarianteDetailRead)
def update_variante(variante_id: int, data: VarianteUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).update(variante_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{variante_id}/activar", response_model=VarianteDetailRead)
def activar_variante(variante_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).set_active(variante_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{variante_id}/desactivar", response_model=VarianteDetailRead)
def desactivar_variante(variante_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).set_active(variante_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/{variante_id}/valores", response_model=list[VarianteValorAtributoRead])
def list_valores_variante(variante_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return VarianteService(db).list_values(variante_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error
