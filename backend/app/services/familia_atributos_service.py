from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import FamiliaAtributo, FamiliaAtributos
from backend.app.repositories import AtributoRepository, FamiliaAtributosRepository
from backend.app.schemas import FamiliaAtributoCreate, FamiliaAtributosCreate, FamiliaAtributosUpdate
from backend.app.services.exceptions import ConflictError, NotFoundError
from backend.app.services.utils import update_model_from_schema


class FamiliaAtributosService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = FamiliaAtributosRepository(db)
        self.atributo_repository = AtributoRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[FamiliaAtributos]:
        return self.repository.list(skip, limit)

    def get(self, familia_id: int) -> FamiliaAtributos:
        familia = self.repository.get(familia_id)
        if familia is None:
            raise NotFoundError("Familia de atributos no encontrada")
        return familia

    def create(self, data: FamiliaAtributosCreate) -> FamiliaAtributos:
        if self.repository.get_by_nombre(data.nombre):
            raise ConflictError("Ya existe una familia de atributos con ese nombre")
        familia = FamiliaAtributos(**data.model_dump())
        self.repository.add(familia)
        self.db.commit()
        self.db.refresh(familia)
        return familia

    def update(self, familia_id: int, data: FamiliaAtributosUpdate) -> FamiliaAtributos:
        familia = self.get(familia_id)
        if data.nombre is not None:
            existing = self.repository.get_by_nombre(data.nombre)
            if existing and existing.id != familia_id:
                raise ConflictError("Ya existe una familia de atributos con ese nombre")
        update_model_from_schema(familia, data)
        self.db.commit()
        self.db.refresh(familia)
        return familia

    def set_active(self, familia_id: int, activo: bool) -> FamiliaAtributos:
        familia = self.get(familia_id)
        familia.activo = activo
        self.db.commit()
        self.db.refresh(familia)
        return familia

    def list_atributos(self, familia_id: int) -> list[FamiliaAtributo]:
        self.get(familia_id)
        return self.repository.list_atributos(familia_id)

    def assign_atributo(self, familia_id: int, data: FamiliaAtributoCreate) -> FamiliaAtributo:
        self.get(familia_id)
        if self.atributo_repository.get(data.atributo_id) is None:
            raise NotFoundError("Atributo no encontrado")
        existing = self.repository.get_familia_atributo(familia_id, data.atributo_id)
        if existing:
            existing.orden = data.orden
            existing.obligatorio = data.obligatorio
            self.db.commit()
            self.db.refresh(existing)
            return existing
        familia_atributo = FamiliaAtributo(familia_id=familia_id, **data.model_dump())
        self.repository.add(familia_atributo)
        self.db.commit()
        self.db.refresh(familia_atributo)
        return familia_atributo

    def remove_atributo(self, familia_id: int, atributo_id: int) -> None:
        self.get(familia_id)
        familia_atributo = self.repository.get_familia_atributo(familia_id, atributo_id)
        if familia_atributo is None:
            raise NotFoundError("Atributo no asignado a la familia")
        self.repository.delete(familia_atributo)
        self.db.commit()
