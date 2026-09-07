from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import DestinoInventario, MovimientoStock, MovimientoStockTipo, StockActual, StockEstado
from backend.app.repositories.base import BaseRepository


class DestinoInventarioRepository(BaseRepository[DestinoInventario]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, DestinoInventario)

    def get_by_codigo(self, codigo: str) -> DestinoInventario | None:
        return self.db.scalar(select(DestinoInventario).where(DestinoInventario.codigo == codigo))


class StockActualRepository(BaseRepository[StockActual]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, StockActual)

    def get_by_key(
        self,
        variante_id: int,
        destino_id: int,
        estado: StockEstado,
        *,
        for_update: bool = False,
    ) -> StockActual | None:
        statement = select(StockActual).where(
            StockActual.variante_id == variante_id,
            StockActual.destino_id == destino_id,
            StockActual.estado == estado,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_filtered(
        self,
        *,
        variante_id: int | None = None,
        destino_id: int | None = None,
        estado: StockEstado | None = None,
    ) -> list[StockActual]:
        statement = select(StockActual).options(
            selectinload(StockActual.variante),
            selectinload(StockActual.destino),
        )
        if variante_id is not None:
            statement = statement.where(StockActual.variante_id == variante_id)
        if destino_id is not None:
            statement = statement.where(StockActual.destino_id == destino_id)
        if estado is not None:
            statement = statement.where(StockActual.estado == estado)
        return list(self.db.scalars(statement.order_by(StockActual.variante_id, StockActual.destino_id)).all())

    def movement_sum(self, variante_id: int, destino_id: int, estado: StockEstado) -> int:
        return int(
            self.db.scalar(
                select(func.coalesce(func.sum(MovimientoStock.cantidad), 0)).where(
                    MovimientoStock.variante_id == variante_id,
                    MovimientoStock.destino_id == destino_id,
                    MovimientoStock.estado == estado,
                )
            )
            or 0
        )


class MovimientoStockRepository(BaseRepository[MovimientoStock]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, MovimientoStock)

    def get_by_global_id(self, global_id: str) -> MovimientoStock | None:
        return self.db.scalar(
            select(MovimientoStock)
            .where(MovimientoStock.global_id == global_id)
            .options(selectinload(MovimientoStock.destino), selectinload(MovimientoStock.variante))
        )

    def list_filtered(
        self,
        *,
        variante_id: int | None = None,
        destino_id: int | None = None,
        tipo: MovimientoStockTipo | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
    ) -> list[MovimientoStock]:
        statement: Select[tuple[MovimientoStock]] = select(MovimientoStock).options(
            selectinload(MovimientoStock.destino),
            selectinload(MovimientoStock.variante),
        )
        if variante_id is not None:
            statement = statement.where(MovimientoStock.variante_id == variante_id)
        if destino_id is not None:
            statement = statement.where(MovimientoStock.destino_id == destino_id)
        if tipo is not None:
            statement = statement.where(MovimientoStock.tipo == tipo)
        if fecha_desde is not None:
            statement = statement.where(MovimientoStock.fecha >= fecha_desde)
        if fecha_hasta is not None:
            statement = statement.where(MovimientoStock.fecha <= fecha_hasta)
        return list(self.db.scalars(statement.order_by(MovimientoStock.fecha, MovimientoStock.id)).all())
