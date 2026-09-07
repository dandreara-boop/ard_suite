from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Atributo, ValorAtributo
from backend.app.repositories.base import BaseRepository


class AtributoRepository(BaseRepository[Atributo]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Atributo)

    def get_by_nombre(self, nombre: str) -> Atributo | None:
        return self.db.scalar(select(Atributo).where(Atributo.nombre == nombre))

    def get_valor(self, valor_id: int) -> ValorAtributo | None:
        return self.db.get(ValorAtributo, valor_id)

    def get_valor_by_atributo_valor(self, atributo_id: int, valor: str) -> ValorAtributo | None:
        return self.db.scalar(
            select(ValorAtributo).where(
                ValorAtributo.atributo_id == atributo_id,
                ValorAtributo.valor == valor,
            )
        )

    def list_valores(self, atributo_id: int | None = None) -> list[ValorAtributo]:
        statement = select(ValorAtributo).order_by(ValorAtributo.orden, ValorAtributo.id)
        if atributo_id is not None:
            statement = statement.where(ValorAtributo.atributo_id == atributo_id)
        return list(self.db.scalars(statement).all())
