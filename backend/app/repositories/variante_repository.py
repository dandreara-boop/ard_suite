from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Variante, VarianteValorAtributo
from backend.app.repositories.base import BaseRepository


class VarianteRepository(BaseRepository[Variante]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Variante)

    def get_by_codigo_barra(self, codigo_barra: str) -> Variante | None:
        return self.db.scalar(
            select(Variante)
            .where(Variante.codigo_barra == codigo_barra)
            .options(selectinload(Variante.articulo), selectinload(Variante.valores))
        )

    def get_with_values(self, variante_id: int) -> Variante | None:
        return self.db.scalar(
            select(Variante)
            .where(Variante.id == variante_id)
            .options(selectinload(Variante.articulo), selectinload(Variante.valores))
        )

    def list_values(self, variante_id: int) -> list[VarianteValorAtributo]:
        return list(
            self.db.scalars(
                select(VarianteValorAtributo).where(
                    VarianteValorAtributo.variante_id == variante_id
                )
            ).all()
        )

    def list_by_articulo_with_values(self, articulo_id: int) -> list[Variante]:
        return list(
            self.db.scalars(
                select(Variante)
                .where(Variante.articulo_id == articulo_id)
                .options(selectinload(Variante.valores))
            ).all()
        )
