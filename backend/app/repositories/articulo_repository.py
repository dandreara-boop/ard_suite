from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Articulo, ArticuloAtributo
from backend.app.repositories.base import BaseRepository


class ArticuloRepository(BaseRepository[Articulo]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Articulo)

    def get_by_codigo(self, codigo: str) -> Articulo | None:
        return self.db.scalar(select(Articulo).where(Articulo.codigo == codigo))

    def get_with_atributos(self, articulo_id: int) -> Articulo | None:
        return self.db.scalar(
            select(Articulo)
            .where(Articulo.id == articulo_id)
            .options(selectinload(Articulo.atributos).selectinload(ArticuloAtributo.atributo))
        )

    def get_articulo_atributo(
        self, articulo_id: int, atributo_id: int
    ) -> ArticuloAtributo | None:
        return self.db.scalar(
            select(ArticuloAtributo).where(
                ArticuloAtributo.articulo_id == articulo_id,
                ArticuloAtributo.atributo_id == atributo_id,
            )
        )

    def list_atributos(self, articulo_id: int) -> list[ArticuloAtributo]:
        return list(
            self.db.scalars(
                select(ArticuloAtributo)
                .where(ArticuloAtributo.articulo_id == articulo_id)
                .order_by(ArticuloAtributo.orden, ArticuloAtributo.id)
            ).all()
        )
