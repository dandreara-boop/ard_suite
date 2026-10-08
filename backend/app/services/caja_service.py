from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from backend.app.models import (
    Caja,
    ArqueoCaja,
    ArqueoCajaEstado,
    ArqueoCajaTipo,
    MedioPago,
    MovimientoCaja,
    MovimientoCajaTipo,
    PagoVenta,
    SesionCaja,
    SesionCajaEstado,
    Venta,
    VentaEstado,
)
from backend.app.schemas.caja import (
    AbrirSesionCajaResponse,
    CerrarSesionCajaRequest,
    CerrarSesionCajaResponse,
    MovimientoCajaCreate,
    SesionCajaResumenRead,
)
from backend.app.services.exceptions import ConflictError, NotFoundError, ValidationError

MOVIMIENTOS_OPERATIVOS = {
    MovimientoCajaTipo.INGRESO,
    MovimientoCajaTipo.RETIRO,
    MovimientoCajaTipo.EGRESO,
}


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

    def registrar_movimiento(self, sesion_caja_id: int, data: MovimientoCajaCreate) -> MovimientoCaja:
        try:
            sesion = self._get_operable_session(sesion_caja_id, usuario_id=data.usuario_id)
            tipo = self._validate_movimiento_tipo(data.tipo)
            motivo = self._normalize_motivo(data.motivo)
            if tipo == MovimientoCajaTipo.EGRESO and motivo is None:
                raise ValidationError("El motivo es obligatorio para egresos de caja")
            movimiento = MovimientoCaja(
                sesion_caja_id=sesion.id,
                tipo=tipo,
                importe=data.importe,
                motivo=motivo,
                usuario_id=data.usuario_id,
            )
            self.db.add(movimiento)
            self.db.commit()
            self.db.refresh(movimiento)
            return movimiento
        except Exception:
            self.db.rollback()
            raise

    def listar_movimientos(self, sesion_caja_id: int) -> list[MovimientoCaja]:
        if self.db.get(SesionCaja, sesion_caja_id) is None:
            raise NotFoundError("Sesion de caja no encontrada")
        return list(
            self.db.scalars(
                select(MovimientoCaja)
                .where(MovimientoCaja.sesion_caja_id == sesion_caja_id)
                .order_by(MovimientoCaja.fecha, MovimientoCaja.id)
            ).all()
        )

    def total_pagos_efectivo_sesion(self, sesion_caja_id: int) -> Decimal:
        if self.db.get(SesionCaja, sesion_caja_id) is None:
            raise NotFoundError("Sesion de caja no encontrada")
        return self._total_pagos_efectivo_sesion(sesion_caja_id)

    def cerrar_sesion(self, sesion_caja_id: int, data: CerrarSesionCajaRequest) -> CerrarSesionCajaResponse:
        try:
            sesion = self.db.scalar(
                select(SesionCaja)
                .where(SesionCaja.id == sesion_caja_id)
                .with_for_update()
            )
            if sesion is None:
                raise NotFoundError("Sesion de caja no encontrada")
            if sesion.estado != SesionCajaEstado.ABIERTA:
                raise ConflictError("La sesion de caja ya esta cerrada")
            if sesion.cajero_id != data.usuario_id:
                raise ValidationError("La sesion de caja pertenece a otro cajero")

            efectivo_esperado = self._efectivo_esperado_sesion(sesion)
            primer_conteo = data.efectivo_final_declarado.quantize(Decimal("0.01"))
            diferencia = (primer_conteo - efectivo_esperado).quantize(Decimal("0.01"))
            now = datetime.now()
            observacion = self._normalize_motivo(data.observacion_cierre)
            arqueo = ArqueoCaja(
                sesion_caja_id=sesion.id,
                tipo=ArqueoCajaTipo.CIERRE,
                efectivo_esperado=efectivo_esperado,
                primer_conteo=primer_conteo,
                diferencia=diferencia,
                fecha=now,
                usuario_id=data.usuario_id,
                estado=ArqueoCajaEstado.REGISTRADO,
            )
            self.db.add(arqueo)
            sesion.estado = SesionCajaEstado.CERRADA
            sesion.cerrada_at = now
            sesion.efectivo_final_declarado = primer_conteo
            sesion.observacion_cierre = observacion
            self.db.flush()
            response = CerrarSesionCajaResponse(
                sesion_caja_id=sesion.id,
                estado=sesion.estado,
                arqueo_id=arqueo.id,
                efectivo_esperado=efectivo_esperado,
                primer_conteo=primer_conteo,
                diferencia=diferencia,
                cerrada_at=now,
                arqueo_fecha=arqueo.fecha,
            )
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise

    def _total_pagos_efectivo_sesion(self, sesion_caja_id: int) -> Decimal:
        total = self.db.scalar(
            select(func.coalesce(func.sum(PagoVenta.importe), Decimal("0.00")))
            .join(Venta, PagoVenta.venta_id == Venta.id)
            .join(MedioPago, PagoVenta.medio_pago_id == MedioPago.id)
            .where(
                Venta.sesion_caja_id == sesion_caja_id,
                Venta.estado == VentaEstado.CERRADA,
                MedioPago.es_efectivo.is_(True),
            )
        )
        return Decimal(total or Decimal("0.00")).quantize(Decimal("0.01"))

    def _efectivo_esperado_sesion(self, sesion: SesionCaja) -> Decimal:
        efectivo_inicial = Decimal(sesion.efectivo_inicial or Decimal("0.00"))
        pagos_efectivo = self._total_pagos_efectivo_sesion(sesion.id)
        ingresos = self._total_movimientos_sesion(sesion.id, MovimientoCajaTipo.INGRESO)
        retiros = self._total_movimientos_sesion(sesion.id, MovimientoCajaTipo.RETIRO)
        egresos = self._total_movimientos_sesion(sesion.id, MovimientoCajaTipo.EGRESO)
        return (efectivo_inicial + pagos_efectivo + ingresos - retiros - egresos).quantize(Decimal("0.01"))

    def _total_movimientos_sesion(self, sesion_caja_id: int, tipo: MovimientoCajaTipo) -> Decimal:
        total = self.db.scalar(
            select(func.coalesce(func.sum(MovimientoCaja.importe), Decimal("0.00")))
            .where(MovimientoCaja.sesion_caja_id == sesion_caja_id, MovimientoCaja.tipo == tipo)
        )
        return Decimal(total or Decimal("0.00")).quantize(Decimal("0.01"))

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

    def _get_operable_session(self, sesion_caja_id: int, *, usuario_id: int) -> SesionCaja:
        sesion = self.db.scalar(
            select(SesionCaja)
            .options(joinedload(SesionCaja.caja))
            .where(SesionCaja.id == sesion_caja_id)
            .with_for_update()
        )
        if sesion is None:
            raise NotFoundError("Sesion de caja no encontrada")
        if sesion.estado != SesionCajaEstado.ABIERTA:
            raise ValidationError("La sesion de caja no esta abierta")
        if sesion.cajero_id != usuario_id:
            raise ValidationError("La sesion de caja pertenece a otro cajero")
        if sesion.caja is None:
            raise NotFoundError("Caja no encontrada")
        if not sesion.caja.activa:
            raise ValidationError("La caja esta inactiva")
        return sesion

    def _validate_movimiento_tipo(self, tipo: MovimientoCajaTipo) -> MovimientoCajaTipo:
        if tipo not in MOVIMIENTOS_OPERATIVOS:
            raise ValidationError("Tipo de movimiento de caja no soportado")
        return tipo

    def _normalize_motivo(self, motivo: str | None) -> str | None:
        if motivo is None:
            return None
        stripped = motivo.strip()
        return stripped or None

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


def impacto_movimiento_caja(tipo: MovimientoCajaTipo, importe: Decimal) -> Decimal:
    if tipo == MovimientoCajaTipo.INGRESO:
        return importe
    if tipo in {MovimientoCajaTipo.RETIRO, MovimientoCajaTipo.EGRESO}:
        return -importe
    raise ValidationError("Tipo de movimiento de caja no soportado")
