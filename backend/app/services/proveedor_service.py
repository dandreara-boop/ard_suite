from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models import ArticuloProveedor, Proveedor
from backend.app.repositories import ArticuloRepository, ProveedorRepository
from backend.app.schemas import ArticuloProveedorCreate, ProveedorCreate, ProveedorUpdate
from backend.app.services.exceptions import ConflictError, NotFoundError
from backend.app.services.utils import update_model_from_schema


class ProveedorService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = ProveedorRepository(db)
        self.articulo_repository = ArticuloRepository(db)

    def list(self, skip: int = 0, limit: int = 100) -> list[Proveedor]:
        return self.repository.list(skip, limit)

    def get(self, proveedor_id: int) -> Proveedor:
        proveedor = self.repository.get(proveedor_id)
        if proveedor is None:
            raise NotFoundError("Proveedor no encontrado")
        return proveedor

    def create(self, data: ProveedorCreate) -> Proveedor:
        if self.repository.get_by_codigo(data.codigo):
            raise ConflictError("Ya existe un proveedor con ese codigo")
        proveedor = Proveedor(**data.model_dump())
        self.repository.add(proveedor)
        self.db.commit()
        self.db.refresh(proveedor)
        return proveedor

    def update(self, proveedor_id: int, data: ProveedorUpdate) -> Proveedor:
        proveedor = self.get(proveedor_id)
        if data.codigo is not None:
            existing = self.repository.get_by_codigo(data.codigo)
            if existing and existing.id != proveedor_id:
                raise ConflictError("Ya existe un proveedor con ese codigo")
        update_model_from_schema(proveedor, data)
        self.db.commit()
        self.db.refresh(proveedor)
        return proveedor

    def set_active(self, proveedor_id: int, activo: bool) -> Proveedor:
        proveedor = self.get(proveedor_id)
        proveedor.activo = activo
        self.db.commit()
        self.db.refresh(proveedor)
        return proveedor

    def associate_articulo(self, data: ArticuloProveedorCreate) -> ArticuloProveedor:
        if self.articulo_repository.get(data.articulo_id) is None:
            raise NotFoundError("Articulo no encontrado")
        self.get(data.proveedor_id)
        existing = self.repository.get_articulo_proveedor(data.articulo_id, data.proveedor_id)
        if existing:
            update_model_from_schema(existing, data)
            self.db.commit()
            self.db.refresh(existing)
            return existing
        articulo_proveedor = ArticuloProveedor(**data.model_dump())
        self.repository.add(articulo_proveedor)
        self.db.commit()
        self.db.refresh(articulo_proveedor)
        return articulo_proveedor

    def list_articulo_proveedores(self, articulo_id: int) -> list[ArticuloProveedor]:
        if self.articulo_repository.get(articulo_id) is None:
            raise NotFoundError("Articulo no encontrado")
        return self.repository.list_articulo_proveedores(articulo_id)
