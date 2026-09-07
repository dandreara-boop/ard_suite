from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from backend.app.api.errors import map_catalog_error
from backend.app.db.session import get_db
from backend.app.schemas import (
    ArticuloAtributoCreate,
    ArticuloAtributoRead,
    ArticuloCreate,
    ArticuloRead,
    ArticuloUpdate,
    VarianteGenerateRequest,
    VarianteDetailRead,
    VariantePreviewItemRead,
    VariantePreviewRequest,
)
from backend.app.services import ArticuloService, VarianteService
from backend.app.services.exceptions import CatalogError

router = APIRouter(prefix="/api/articulos", tags=["articulos"])


@router.get("", response_model=list[ArticuloRead])
def list_articulos(db: Annotated[Session, Depends(get_db)], skip: int = 0, limit: int = 100):
    return ArticuloService(db).list(skip, limit)


@router.get("/{articulo_id}", response_model=ArticuloRead)
def get_articulo(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).get(articulo_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("", response_model=ArticuloRead, status_code=201)
def create_articulo(data: ArticuloCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).create(data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.put("/{articulo_id}", response_model=ArticuloRead)
def update_articulo(articulo_id: int, data: ArticuloUpdate, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).update(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{articulo_id}/activar", response_model=ArticuloRead)
def activar_articulo(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).set_active(articulo_id, True)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.patch("/{articulo_id}/desactivar", response_model=ArticuloRead)
def desactivar_articulo(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).set_active(articulo_id, False)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.get("/{articulo_id}/atributos", response_model=list[ArticuloAtributoRead])
def list_atributos_articulo(articulo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return ArticuloService(db).list_atributos(articulo_id)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{articulo_id}/atributos", response_model=ArticuloAtributoRead, status_code=201)
def assign_atributo(
    articulo_id: int,
    data: ArticuloAtributoCreate,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return ArticuloService(db).assign_atributo(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.delete("/{articulo_id}/atributos/{atributo_id}", status_code=204)
def remove_atributo(articulo_id: int, atributo_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        ArticuloService(db).remove_atributo(articulo_id, atributo_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{articulo_id}/variantes/preview", response_model=list[VariantePreviewItemRead])
def preview_variantes(
    articulo_id: int,
    data: VariantePreviewRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return VarianteService(db).preview_for_articulo(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error


@router.post("/{articulo_id}/variantes/generate", response_model=list[VarianteDetailRead], status_code=201)
def generate_variantes(
    articulo_id: int,
    data: VarianteGenerateRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return VarianteService(db).generate_for_articulo(articulo_id, data)
    except CatalogError as error:
        raise map_catalog_error(error) from error
