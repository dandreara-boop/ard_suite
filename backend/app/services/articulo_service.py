from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import Articulo, ArticuloAtributo
from backend.app.repositories import (
    ArticuloRepository,
    AtributoRepository,
    CategoriaRepository,
    FamiliaAtributosRepository,
    MarcaRepository,
)
from backend.app.schemas import ArticuloAtributoCreate, ArticuloCreate, ArticuloUpdate
from backend.app.services.exceptions import ConflictError, NotFoundError
from backend.app.services.utils import update_model_from_schema


class ArticuloService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = ArticuloRepository(db)
        self.categoria_repository = CategoriaRepository(db)
        self.marca_repository = MarcaRepository(db)
        self.familia_repository = FamiliaAtributosRepository(db)
        self.atributo_repository = AtributoRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[Articulo]:
        return self.repository.list(skip, limit)

    def get(self, articulo_id: int) -> Articulo:
        articulo = self.repository.get(articulo_id)
        if articulo is None:
            raise NotFoundError("Articulo no encontrado")
        return articulo

    def create(self, data: ArticuloCreate) -> Articulo:
        if self.repository.get_by_codigo(data.codigo):
            raise ConflictError("Ya existe un articulo con ese codigo")
        if self.categoria_repository.get(data.categoria_id) is None:
            raise NotFoundError("Categoria no encontrada")
        if data.marca_id is not None and self.marca_repository.get(data.marca_id) is None:
            raise NotFoundError("Marca no encontrada")
        if (
            data.familia_atributos_id is not None
            and self.familia_repository.get(data.familia_atributos_id) is None
        ):
            raise NotFoundError("Familia de atributos no encontrada")
        articulo = Articulo(**data.model_dump())
        self.repository.add(articulo)
        self.db.commit()
        self.db.refresh(articulo)
        return articulo

    def update(self, articulo_id: int, data: ArticuloUpdate) -> Articulo:
        articulo = self.get(articulo_id)
        if data.codigo is not None:
            existing = self.repository.get_by_codigo(data.codigo)
            if existing and existing.id != articulo_id:
                raise ConflictError("Ya existe un articulo con ese codigo")
        if data.categoria_id is not None and self.categoria_repository.get(data.categoria_id) is None:
            raise NotFoundError("Categoria no encontrada")
        if data.marca_id is not None and self.marca_repository.get(data.marca_id) is None:
            raise NotFoundError("Marca no encontrada")
        if (
            data.familia_atributos_id is not None
            and self.familia_repository.get(data.familia_atributos_id) is None
        ):
            raise NotFoundError("Familia de atributos no encontrada")
        update_model_from_schema(articulo, data)
        self.db.commit()
        self.db.refresh(articulo)
        return articulo

    def set_active(self, articulo_id: int, activo: bool) -> Articulo:
        articulo = self.get(articulo_id)
        articulo.activo = activo
        self.db.commit()
        self.db.refresh(articulo)
        return articulo

    def list_atributos(self, articulo_id: int) -> list[ArticuloAtributo]:
        self.get(articulo_id)
        return self.repository.list_atributos(articulo_id)

    def assign_atributo(self, articulo_id: int, data: ArticuloAtributoCreate) -> ArticuloAtributo:
        self.get(articulo_id)
        if self.atributo_repository.get(data.atributo_id) is None:
            raise NotFoundError("Atributo no encontrado")
        existing = self.repository.get_articulo_atributo(articulo_id, data.atributo_id)
        if existing:
            existing.orden = data.orden
            existing.obligatorio = data.obligatorio
            self.db.commit()
            self.db.refresh(existing)
            return existing
        articulo_atributo = ArticuloAtributo(articulo_id=articulo_id, **data.model_dump())
        self.repository.add(articulo_atributo)
        self.db.commit()
        self.db.refresh(articulo_atributo)
        return articulo_atributo

    def remove_atributo(self, articulo_id: int, atributo_id: int) -> None:
        self.get(articulo_id)
        articulo_atributo = self.repository.get_articulo_atributo(articulo_id, atributo_id)
        if articulo_atributo is None:
            raise NotFoundError("Atributo no asignado al articulo")
        self.repository.delete(articulo_atributo)
        self.db.commit()
