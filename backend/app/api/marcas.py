from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import MarcaCreate, MarcaRead, MarcaUpdate
from backend.app.services import MarcaService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/marcas", tags=["marcas"])


@router.get("", response_model=list[MarcaRead])
def list_marcas(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return MarcaService(db).list(skip, limit)


@router.get("/{marca_id}", response_model=MarcaRead)
def get_marca(marca_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return MarcaService(db).get(marca_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=MarcaRead, status_code=201)
def create_marca(data: MarcaCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return MarcaService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{marca_id}", response_model=MarcaRead)
def update_marca(marca_id: int, data: MarcaUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return MarcaService(db).update(marca_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{marca_id}/activar", response_model=MarcaRead)
def activar_marca(marca_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return MarcaService(db).set_active(marca_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{marca_id}/desactivar", response_model=MarcaRead)
def desactivar_marca(marca_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return MarcaService(db).set_active(marca_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error
