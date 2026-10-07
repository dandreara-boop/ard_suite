from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.models import Caja, SesionCaja, SesionCajaEstado
from backend.app.schemas.caja import AbrirSesionCajaResponse, SesionCajaResumenRead
from backend.app.services.exceptions import ConflictError, NotFoundError, ValidationError


class CajaService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def abrir_sesion(self, caja_id: int, *, cajero_id: int, efectivo_inicial: Decimal) -> AbrirSesionCajaResponse:
        if efectivo_inicial < 0:
            raise ValidationError("El efectivo inicial debe ser mayor o igual a cero")
        try:
            self._begin_write_lock_if_sqlite()
            caja = self.db.scalar(select(Caja).where(Caja.id == caja_id).with_for_update())
            if caja is None:
                raise NotFoundError("Caja no encontrada")
            if not caja.activa:
                raise ValidationError("La caja esta inactiva")
            existing = self.db.scalar(
                select(SesionCaja)
                .where(SesionCaja.caja_id == caja.id, SesionCaja.estado == SesionCajaEstado.ABIERTA)
                .with_for_update()
            )
            if existing is not None:
                raise ConflictError("La caja ya tiene una sesion abierta")
            otras = list(
                self.db.scalars(
                    self._sesiones_abiertas_statement(cajero_id).where(SesionCaja.caja_id != caja.id)
                ).all()
            )
            sesion = SesionCaja(caja=caja, cajero_id=cajero_id, efectivo_inicial=efectivo_inicial)
            self.db.add(sesion)
            self.db.commit()
            sesion = self.get_sesion(sesion.id)
            return AbrirSesionCajaResponse(
                sesion=sesion,
                advertencia_multiples_sesiones=bool(otras),
                otras_sesiones_abiertas=[self._resumen(item) for item in otras],
            )
        except Exception:
            self.db.rollback()
            raise

    def listar_abiertas_por_cajero(self, cajero_id: int) -> list[SesionCaja]:
        return list(self.db.scalars(self._sesiones_abiertas_statement(cajero_id)).all())

    def get_sesion(self, sesion_id: int) -> SesionCaja:
        sesion = self.db.scalar(
            select(SesionCaja).options(joinedload(SesionCaja.caja)).where(SesionCaja.id == sesion_id)
        )
        if sesion is None:
            raise NotFoundError("Sesion de caja no encontrada")
        return sesion

    def _sesiones_abiertas_statement(self, cajero_id: int):
        return (
            select(SesionCaja)
            .options(joinedload(SesionCaja.caja))
            .where(SesionCaja.cajero_id == cajero_id, SesionCaja.estado == SesionCajaEstado.ABIERTA)
            .order_by(SesionCaja.abierta_at, SesionCaja.id)
        )

    def _resumen(self, sesion: SesionCaja) -> SesionCajaResumenRead:
        return SesionCajaResumenRead(
            sesion_id=sesion.id,
            caja_id=sesion.caja_id,
            caja_codigo=sesion.caja.codigo,
            caja_nombre=sesion.caja.nombre,
        )

    def _begin_write_lock_if_sqlite(self) -> None:
        bind = self.db.get_bind()
        if bind.dialect.name != "sqlite":
            return
        self.db.connection().exec_driver_sql("BEGIN IMMEDIATE")
