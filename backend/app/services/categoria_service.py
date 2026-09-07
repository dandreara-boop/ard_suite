from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import Categoria
from backend.app.repositories import CategoriaRepository
from backend.app.schemas import CategoriaCreate, CategoriaUpdate
from backend.app.services.exceptions import NotFoundError, ValidationError
from backend.app.services.utils import update_model_from_schema


class CategoriaService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = CategoriaRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[Categoria]:
        return self.repository.list(skip, limit)

    def get(self, categoria_id: int) -> Categoria:
        categoria = self.repository.get(categoria_id)
        if categoria is None:
            raise NotFoundError("Categoria no encontrada")
        return categoria

    def create(self, data: CategoriaCreate) -> Categoria:
        if data.parent_id is not None:
            self.get(data.parent_id)
        categoria = Categoria(**data.model_dump())
        self.repository.add(categoria)
        self.db.commit()
        self.db.refresh(categoria)
        return categoria

    def update(self, categoria_id: int, data: CategoriaUpdate) -> Categoria:
        categoria = self.get(categoria_id)
        if data.parent_id == categoria_id:
            raise ValidationError("Una categoria no puede ser padre de si misma")
        if data.parent_id is not None:
            self.get(data.parent_id)
        update_model_from_schema(categoria, data)
        self.db.commit()
        self.db.refresh(categoria)
        return categoria

    def set_active(self, categoria_id: int, activo: bool) -> Categoria:
        categoria = self.get(categoria_id)
        categoria.activo = activo
        self.db.commit()
        self.db.refresh(categoria)
        return categoria
