from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import (
    FamiliaAtributoCreate,
    FamiliaAtributoRead,
    FamiliaAtributosCreate,
    FamiliaAtributosRead,
    FamiliaAtributosUpdate,
)
from backend.app.services import FamiliaAtributosService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/familias-atributos", tags=["familias-atributos"])


@router.get("", response_model=list[FamiliaAtributosRead])
def list_familias(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return FamiliaAtributosService(db).list(skip, limit)


@router.get("/{familia_id}", response_model=FamiliaAtributosRead)
def get_familia(familia_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return FamiliaAtributosService(db).get(familia_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=FamiliaAtributosRead, status_code=201)
def create_familia(data: FamiliaAtributosCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return FamiliaAtributosService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{familia_id}", response_model=FamiliaAtributosRead)
def update_familia(
    familia_id: int,
    data: FamiliaAtributosUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return FamiliaAtributosService(db).update(familia_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{familia_id}/activar", response_model=FamiliaAtributosRead)
def activar_familia(familia_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return FamiliaAtributosService(db).set_active(familia_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{familia_id}/desactivar", response_model=FamiliaAtributosRead)
def desactivar_familia(familia_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return FamiliaAtributosService(db).set_active(familia_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/{familia_id}/atributos", response_model=list[FamiliaAtributoRead])
def list_atributos_familia(familia_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return FamiliaAtributosService(db).list_atributos(familia_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{familia_id}/atributos", response_model=FamiliaAtributoRead, status_code=201)
def assign_atributo(
    familia_id: int,
    data: FamiliaAtributoCreate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return FamiliaAtributosService(db).assign_atributo(familia_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.delete("/{familia_id}/atributos/{atributo_id}", status_code=204)
def remove_atributo(familia_id: int, atributo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        FamiliaAtributosService(db).remove_atributo(familia_id, atributo_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except CatalogError as error:
        raise map_catalog_error(error) from error
