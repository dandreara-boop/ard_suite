from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.commercial import MedioPago, ResolucionComercialVenta
from backend.app.repositories.base import BaseRepository


class MedioPagoRepository(BaseRepository[MedioPago]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, MedioPago)

    def get_by_codigo(self, codigo: str) -> MedioPago | None:
        return self.db.scalar(select(MedioPago).where(MedioPago.codigo == codigo))

    def get_efectivo(self, *, exclude_id: int | None = None, for_update: bool = False) -> MedioPago | None:
        statement = select(MedioPago).where(MedioPago.es_efectivo.is_(True))
        if exclude_id is not None:
            statement = statement.where(MedioPago.id != exclude_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_ordered(self, active_only: bool = False) -> list[MedioPago]:
        statement = select(MedioPago).order_by(MedioPago.codigo, MedioPago.id)
        if active_only:
            statement = statement.where(MedioPago.activo.is_(True))
        return list(self.db.scalars(statement).all())


class ResolucionComercialVentaRepository(BaseRepository[ResolucionComercialVenta]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, ResolucionComercialVenta)

    def get_by_venta(self, venta_id: int) -> ResolucionComercialVenta | None:
        return self.db.scalar(select(ResolucionComercialVenta).where(ResolucionComercialVenta.venta_id == venta_id))
