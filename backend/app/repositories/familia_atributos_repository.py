from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import FamiliaAtributo, FamiliaAtributos
from backend.app.repositories.base import BaseRepository


class FamiliaAtributosRepository(BaseRepository[FamiliaAtributos]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, FamiliaAtributos)

    def get_by_nombre(self, nombre: str) -> FamiliaAtributos | None:
        return self.db.scalar(select(FamiliaAtributos).where(FamiliaAtributos.nombre == nombre))

    def get_familia_atributo(
        self, familia_id: int, atributo_id: int
    ) -> FamiliaAtributo | None:
        return self.db.scalar(
            select(FamiliaAtributo).where(
                FamiliaAtributo.familia_id == familia_id,
                FamiliaAtributo.atributo_id == atributo_id,
            )
        )

    def list_atributos(self, familia_id: int) -> list[FamiliaAtributo]:
        return list(
            self.db.scalars(
                select(FamiliaAtributo)
                .where(FamiliaAtributo.familia_id == familia_id)
                .order_by(FamiliaAtributo.orden, FamiliaAtributo.id)
            ).all()
        )
