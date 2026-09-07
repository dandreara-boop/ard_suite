from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import CategoriaCreate, CategoriaRead, CategoriaUpdate
from backend.app.services import CategoriaService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/categorias", tags=["categorias"])


@router.get("", response_model=list[CategoriaRead])
def list_categorias(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return CategoriaService(db).list(skip, limit)


@router.get("/{categoria_id}", response_model=CategoriaRead)
def get_categoria(categoria_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CategoriaService(db).get(categoria_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=CategoriaRead, status_code=201)
def create_categoria(data: CategoriaCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return CategoriaService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{categoria_id}", response_model=CategoriaRead)
def update_categoria(categoria_id: int, data: CategoriaUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return CategoriaService(db).update(categoria_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{categoria_id}/activar", response_model=CategoriaRead)
def activar_categoria(categoria_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CategoriaService(db).set_active(categoria_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{categoria_id}/desactivar", response_model=CategoriaRead)
def desactivar_categoria(categoria_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return CategoriaService(db).set_active(categoria_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error
