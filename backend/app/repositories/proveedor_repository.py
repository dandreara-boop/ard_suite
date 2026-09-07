from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ArticuloProveedor, Proveedor
from backend.app.repositories.base import BaseRepository


class ProveedorRepository(BaseRepository[Proveedor]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Proveedor)

    def get_by_codigo(self, codigo: str) -> Proveedor | None:
        return self.db.scalar(select(Proveedor).where(Proveedor.codigo == codigo))

    def get_articulo_proveedor(
        self, articulo_id: int, proveedor_id: int
    ) -> ArticuloProveedor | None:
        return self.db.scalar(
            select(ArticuloProveedor).where(
                ArticuloProveedor.articulo_id == articulo_id,
                ArticuloProveedor.proveedor_id == proveedor_id,
            )
        )

    def list_articulo_proveedores(self, articulo_id: int) -> list[ArticuloProveedor]:
        return list(
            self.db.scalars(
                select(ArticuloProveedor).where(ArticuloProveedor.articulo_id == articulo_id)
            ).all()
        )
