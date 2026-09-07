from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import Marca
from backend.app.repositories import MarcaRepository
from backend.app.schemas import MarcaCreate, MarcaUpdate
from backend.app.services.exceptions import ConflictError, NotFoundError
from backend.app.services.utils import update_model_from_schema


class MarcaService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = MarcaRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[Marca]:
        return self.repository.list(skip, limit)

    def get(self, marca_id: int) -> Marca:
        marca = self.repository.get(marca_id)
        if marca is None:
            raise NotFoundError("Marca no encontrada")
        return marca

    def create(self, data: MarcaCreate) -> Marca:
        if self.repository.get_by_nombre(data.nombre):
            raise ConflictError("Ya existe una marca con ese nombre")
        marca = Marca(**data.model_dump())
        self.repository.add(marca)
        self.db.commit()
        self.db.refresh(marca)
        return marca

    def update(self, marca_id: int, data: MarcaUpdate) -> Marca:
        marca = self.get(marca_id)
        if data.nombre is not None:
            existing = self.repository.get_by_nombre(data.nombre)
            if existing and existing.id != marca_id:
                raise ConflictError("Ya existe una marca con ese nombre")
        update_model_from_schema(marca, data)
        self.db.commit()
        self.db.refresh(marca)
        return marca

    def set_active(self, marca_id: int, activo: bool) -> Marca:
        marca = self.get(marca_id)
        marca.activo = activo
        self.db.commit()
        self.db.refresh(marca)
        return marca
