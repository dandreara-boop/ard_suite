from sqlalchemy.orm import Session

from backend.app.models import Categoria
from backend.app.repositories.base import BaseRepository


class CategoriaRepository(BaseRepository[Categoria]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Categoria)
