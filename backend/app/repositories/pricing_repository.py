from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Articulo
from backend.app.models.pricing import (
    AuditoriaPrecioArticulo,
    CondicionComercialPrecio,
    CondicionPrecioTipo,
    OrigenPrecioArticulo,
    PrecioArticulo,
)
from backend.app.repositories.base import BaseRepository


class CondicionComercialPrecioRepository(BaseRepository[CondicionComercialPrecio]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, CondicionComercialPrecio)

    def list_ordered(self, active_only: bool = False) -> list[CondicionComercialPrecio]:
        statement = select(CondicionComercialPrecio).order_by(
            CondicionComercialPrecio.orden, CondicionComercialPrecio.id
        )
        if active_only:
            statement = statement.where(CondicionComercialPrecio.activa.is_(True))
        return list(self.db.scalars(statement).all())

    def get_by_codigo(self, codigo: str) -> CondicionComercialPrecio | None:
        return self.db.scalar(select(CondicionComercialPrecio).where(CondicionComercialPrecio.codigo == codigo))

    def get_active_base(self) -> CondicionComercialPrecio | None:
        return self.db.scalar(
            select(CondicionComercialPrecio).where(
                CondicionComercialPrecio.tipo == CondicionPrecioTipo.BASE,
                CondicionComercialPrecio.activa.is_(True),
            )
        )

    def list_active_derived(self) -> list[CondicionComercialPrecio]:
        return list(
            self.db.scalars(
                select(CondicionComercialPrecio)
                .where(
                    CondicionComercialPrecio.tipo == CondicionPrecioTipo.DERIVADA,
                    CondicionComercialPrecio.activa.is_(True),
                )
                .order_by(CondicionComercialPrecio.orden, CondicionComercialPrecio.id)
            ).all()
        )


class PrecioArticuloRepository(BaseRepository[PrecioArticulo]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, PrecioArticulo)

    def get_by_articulo_condicion(self, articulo_id: int, condicion_id: int) -> PrecioArticulo | None:
        return self.db.scalar(
            select(PrecioArticulo)
            .where(
                PrecioArticulo.articulo_id == articulo_id,
                PrecioArticulo.condicion_comercial_id == condicion_id,
            )
            .options(selectinload(PrecioArticulo.condicion_comercial))
        )

    def list_by_articulo(self, articulo_id: int) -> list[PrecioArticulo]:
        return list(
            self.db.scalars(
                select(PrecioArticulo)
                .where(PrecioArticulo.articulo_id == articulo_id)
                .options(selectinload(PrecioArticulo.condicion_comercial))
                .order_by(PrecioArticulo.condicion_comercial_id)
            ).all()
        )

    def list_for_condition(self, condicion_id: int) -> list[PrecioArticulo]:
        return list(
            self.db.scalars(
                select(PrecioArticulo)
                .where(PrecioArticulo.condicion_comercial_id == condicion_id)
                .options(selectinload(PrecioArticulo.condicion_comercial))
                .order_by(PrecioArticulo.articulo_id)
            ).all()
        )

    def list_with_articles_for_condition(self, condicion_id: int) -> list[PrecioArticulo]:
        return list(
            self.db.scalars(
                select(PrecioArticulo)
                .join(Articulo, Articulo.id == PrecioArticulo.articulo_id)
                .where(PrecioArticulo.condicion_comercial_id == condicion_id)
                .options(selectinload(PrecioArticulo.articulo), selectinload(PrecioArticulo.condicion_comercial))
                .order_by(Articulo.codigo, Articulo.id)
            ).all()
        )

    def count_by_origin(self, condicion_id: int, origen: OrigenPrecioArticulo) -> int:
        return len([price for price in self.list_for_condition(condicion_id) if price.origen == origen])


class AuditoriaPrecioArticuloRepository(BaseRepository[AuditoriaPrecioArticulo]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, AuditoriaPrecioArticulo)
