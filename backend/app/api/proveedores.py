from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import (
    ArticuloProveedorCreate,
    ArticuloProveedorRead,
    ProveedorCreate,
    ProveedorRead,
    ProveedorUpdate,
)
from backend.app.services import ProveedorService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/proveedores", tags=["proveedores"])


@router.get("", response_model=list[ProveedorRead])
def list_proveedores(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return ProveedorService(db).list(skip, limit)


@router.get("/{proveedor_id}", response_model=ProveedorRead)
def get_proveedor(proveedor_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).get(proveedor_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=ProveedorRead, status_code=201)
def create_proveedor(data: ProveedorCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{proveedor_id}", response_model=ProveedorRead)
def update_proveedor(
    proveedor_id: int,
    data: ProveedorUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return ProveedorService(db).update(proveedor_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{proveedor_id}/activar", response_model=ProveedorRead)
def activar_proveedor(proveedor_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).set_active(proveedor_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{proveedor_id}/desactivar", response_model=ProveedorRead)
def desactivar_proveedor(proveedor_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).set_active(proveedor_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/articulos", response_model=ArticuloProveedorRead, status_code=201)
def associate_articulo(data: ArticuloProveedorCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).associate_articulo(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/articulos/{articulo_id}", response_model=list[ArticuloProveedorRead])
def list_articulo_proveedores(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ProveedorService(db).list_articulo_proveedores(articulo_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error
