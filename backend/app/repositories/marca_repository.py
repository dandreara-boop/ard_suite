from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Marca
from backend.app.repositories.base import BaseRepository


class MarcaRepository(BaseRepository[Marca]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Marca)

    def get_by_nombre(self, nombre: str) -> Marca | None:
        return self.db.scalar(select(Marca).where(Marca.nombre == nombre))
