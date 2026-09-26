from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import DetalleVenta, EventoPendiente, EventoPendienteEstado, PagoVenta, Venta
from backend.app.repositories.base import BaseRepository


class VentaRepository(BaseRepository[Venta]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Venta)

    def get_full(self, venta_id: int, *, for_update: bool = False) -> Venta | None:
        statement = (
            select(Venta)
            .where(Venta.id == venta_id)
            .options(selectinload(Venta.detalles).selectinload(DetalleVenta.variante), selectinload(Venta.pagos))
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_full(self, skip: int = 0, limit: int = 100) -> list[Venta]:
        statement: Select[tuple[Venta]] = (
            select(Venta)
            .options(selectinload(Venta.detalles).selectinload(DetalleVenta.variante), selectinload(Venta.pagos))
            .order_by(Venta.id)
            .offset(skip)
            .limit(limit)
        )
        return list(self.db.scalars(statement).all())


class DetalleVentaRepository(BaseRepository[DetalleVenta]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, DetalleVenta)


class PagoVentaRepository(BaseRepository[PagoVenta]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, PagoVenta)


class EventoPendienteRepository(BaseRepository[EventoPendiente]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, EventoPendiente)

    def get_next_pending(
        self, *, for_update: bool = False, exclude_ids: set[int] | None = None
    ) -> EventoPendiente | None:
        statement = (
            select(EventoPendiente)
            .where(EventoPendiente.estado.in_([EventoPendienteEstado.PENDIENTE, EventoPendienteEstado.ERROR]))
            .order_by(EventoPendiente.created_at, EventoPendiente.id)
            .limit(1)
        )
        if exclude_ids:
            statement = statement.where(EventoPendiente.id.not_in(exclude_ids))
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_filtered(
        self,
        *,
        estado: EventoPendienteEstado | None = None,
        tipo: str | None = None,
    ) -> list[EventoPendiente]:
        statement = select(EventoPendiente)
        if estado is not None:
            statement = statement.where(EventoPendiente.estado == estado)
        if tipo is not None:
            statement = statement.where(EventoPendiente.tipo == tipo)
        return list(self.db.scalars(statement.order_by(EventoPendiente.id)).all())
