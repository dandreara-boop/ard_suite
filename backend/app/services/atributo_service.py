from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import Atributo, ValorAtributo
from backend.app.repositories import AtributoRepository
from backend.app.schemas import AtributoCreate, AtributoUpdate, ValorAtributoCreate, ValorAtributoUpdate
from backend.app.services.exceptions import ConflictError, NotFoundError
from backend.app.services.utils import update_model_from_schema


class AtributoService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AtributoRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[Atributo]:
        return self.repository.list(skip, limit)

    def get(self, atributo_id: int) -> Atributo:
        atributo = self.repository.get(atributo_id)
        if atributo is None:
            raise NotFoundError("Atributo no encontrado")
        return atributo

    def create(self, data: AtributoCreate) -> Atributo:
        if self.repository.get_by_nombre(data.nombre):
            raise ConflictError("Ya existe un atributo con ese nombre")
        atributo = Atributo(**data.model_dump())
        self.repository.add(atributo)
        self.db.commit()
        self.db.refresh(atributo)
        return atributo

    def update(self, atributo_id: int, data: AtributoUpdate) -> Atributo:
        atributo = self.get(atributo_id)
        if data.nombre is not None:
            existing = self.repository.get_by_nombre(data.nombre)
            if existing and existing.id != atributo_id:
                raise ConflictError("Ya existe un atributo con ese nombre")
        update_model_from_schema(atributo, data)
        self.db.commit()
        self.db.refresh(atributo)
        return atributo

    def set_active(self, atributo_id: int, activo: bool) -> Atributo:
        atributo = self.get(atributo_id)
        atributo.activo = activo
        self.db.commit()
        self.db.refresh(atributo)
        return atributo

    def list_valores(self, atributo_id: int | None = None) -> list[ValorAtributo]:
        return self.repository.list_valores(atributo_id)

    def get_valor(self, valor_id: int) -> ValorAtributo:
        valor = self.repository.get_valor(valor_id)
        if valor is None:
            raise NotFoundError("Valor de atributo no encontrado")
        return valor

    def create_valor(self, data: ValorAtributoCreate) -> ValorAtributo:
        self.get(data.atributo_id)
        if self.repository.get_valor_by_atributo_valor(data.atributo_id, data.valor):
            raise ConflictError("Ya existe ese valor para el atributo")
        valor = ValorAtributo(**data.model_dump())
        self.repository.add(valor)
        self.db.commit()
        self.db.refresh(valor)
        return valor

    def update_valor(self, valor_id: int, data: ValorAtributoUpdate) -> ValorAtributo:
        valor = self.get_valor(valor_id)
        if data.valor is not None:
            existing = self.repository.get_valor_by_atributo_valor(valor.atributo_id, data.valor)
            if existing and existing.id != valor_id:
                raise ConflictError("Ya existe ese valor para el atributo")
        update_model_from_schema(valor, data)
        self.db.commit()
        self.db.refresh(valor)
        return valor
