from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import (
    AtributoCreate,
    AtributoRead,
    AtributoUpdate,
    ValorAtributoCreate,
    ValorAtributoRead,
    ValorAtributoUpdate,
)
from backend.app.services import AtributoService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/atributos", tags=["atributos"])


@router.get("", response_model=list[AtributoRead])
def list_atributos(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return AtributoService(db).list(skip, limit)


@router.get("/valores", response_model=list[ValorAtributoRead])
def list_valores(db: Annotated[Session, Depends(get_db)], atributo_id: int | None = None):
    return AtributoService(db).list_valores(atributo_id)


@router.get("/{atributo_id}", response_model=AtributoRead)
def get_atributo(atributo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).get(atributo_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=AtributoRead, status_code=201)
def create_atributo(data: AtributoCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{atributo_id}", response_model=AtributoRead)
def update_atributo(atributo_id: int, data: AtributoUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).update(atributo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{atributo_id}/activar", response_model=AtributoRead)
def activar_atributo(atributo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).set_active(atributo_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{atributo_id}/desactivar", response_model=AtributoRead)
def desactivar_atributo(atributo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).set_active(atributo_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/valores", response_model=ValorAtributoRead, status_code=201)
def create_valor(data: ValorAtributoCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).create_valor(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/valores/{valor_id}", response_model=ValorAtributoRead)
def update_valor(valor_id: int, data: ValorAtributoUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return AtributoService(db).update_valor(valor_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error
