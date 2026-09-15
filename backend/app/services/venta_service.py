from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.business.rules import RuleStatus
from backend.app.business.sales import SaleFinalizationContext, SaleFinalizationPolicy
from backend.app.business.sales.sale_rules import SaleLineContext
from backend.app.models import (
    DetalleVenta,
    DestinoInventario,
    EventoPendiente,
    EventoPendienteEstado,
    MovimientoStockTipo,
    PagoVenta,
    StockEstado,
    Variante,
    Venta,
    VentaEstado,
)
from backend.app.repositories.venta_repository import (
    DetalleVentaRepository,
    EventoPendienteRepository,
    PagoVentaRepository,
    VentaRepository,
)
from backend.app.schemas import MovimientoStockCreate
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate
from backend.app.services.exceptions import BusinessRuleViolation, NotFoundError, ValidationError
from backend.app.services.inventory_service import InventoryService

VENTA_FINALIZADA = "VENTA_FINALIZADA"
SALE_MOVEMENT_NAMESPACE = UUID("8a248879-2d85-4e15-98f1-c769b5ed77cb")
MONEY = Decimal("0.01")
QTY = Decimal("0.001")


@dataclass(frozen=True)
class EventProcessingResult:
    procesados: int
    errores: int


class VentaService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ventas = VentaRepository(db)
        self.detalles = DetalleVentaRepository(db)
        self.pagos = PagoVentaRepository(db)
        self.eventos = EventoPendienteRepository(db)
        self.policy = SaleFinalizationPolicy()

    def list(self, skip: int = 0, limit: int = 100) -> list[Venta]:
        return self.ventas.list_full(skip, limit)

    def get(self, venta_id: int) -> Venta:
        venta = self.ventas.get_full(venta_id)
        if venta is None:
            raise NotFoundError("Venta no encontrada")
        return venta

    def create(self, data: VentaCreate) -> Venta:
        if self.db.get(DestinoInventario, data.destino_id) is None:
            raise NotFoundError("Destino de inventario no encontrado")
        venta = Venta(
            numero_venta=f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            destino_id=data.destino_id,
            usuario_id=data.usuario_id,
        )
        try:
            self.db.add(venta)
            self.db.flush()
            venta.numero_venta = f"V-{venta.id:08d}"
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def add_item(self, venta_id: int, data: DetalleVentaCreate) -> Venta:
        venta = self._get_editable_sale(venta_id)
        variante = self.db.get(Variante, data.variante_id)
        if variante is None or not variante.activo:
            raise NotFoundError("Variante no encontrada")
        if variante.articulo is None:
            raise ValidationError("La variante no tiene articulo asociado")

        cantidad = self._quantize_qty(data.cantidad)
        precio_unitario = self._quantize_money(data.precio_unitario)
        importe = self._quantize_money(cantidad * precio_unitario)
        detalle = DetalleVenta(
            venta_id=venta.id,
            variante_id=variante.id,
            codigo_articulo=variante.articulo.codigo,
            codigo_barra=variante.codigo_barra,
            descripcion=variante.articulo.nombre,
            cantidad=cantidad,
            precio_unitario=precio_unitario,
            importe=importe,
        )
        try:
            venta.detalles.append(detalle)
            self.db.add(detalle)
            self._recalculate_totals(venta)
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def remove_item(self, venta_id: int, item_id: int) -> Venta:
        venta = self._get_editable_sale(venta_id)
        item = self.detalles.get(item_id)
        if item is None or item.venta_id != venta.id:
            raise NotFoundError("Item de venta no encontrado")
        try:
            self.detalles.delete(item)
            self._recalculate_totals(venta)
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def add_pago(self, venta_id: int, data: PagoVentaCreate) -> Venta:
        venta = self._get_editable_sale(venta_id)
        pago = PagoVenta(
            venta_id=venta.id,
            medio_pago=data.medio_pago,
            importe=self._quantize_money(data.importe),
        )
        try:
            venta.pagos.append(pago)
            self.db.add(pago)
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def remove_pago(self, venta_id: int, pago_id: int) -> Venta:
        venta = self._get_editable_sale(venta_id)
        pago = self.pagos.get(pago_id)
        if pago is None or pago.venta_id != venta.id:
            raise NotFoundError("Pago de venta no encontrado")
        try:
            self.pagos.delete(pago)
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def finalizar(self, venta_id: int) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            self._recalculate_totals(venta)
            self._ensure_can_finalize(venta)
            venta.estado = VentaEstado.CERRADA
            venta.cerrada_at = datetime.now()
            evento = EventoPendiente(
                tipo=VENTA_FINALIZADA,
                aggregate_type="VENTA",
                aggregate_id=venta.global_id,
                payload={
                    "venta_id": venta.id,
                    "venta_global_id": venta.global_id,
                    "destino_id": venta.destino_id,
                },
            )
            self.db.add(evento)
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def list_eventos(
        self,
        *,
        estado: EventoPendienteEstado | None = None,
        tipo: str | None = None,
    ) -> list[EventoPendiente]:
        return self.eventos.list_filtered(estado=estado, tipo=tipo)

    def procesar_eventos_pendientes(self, limit: int = 10) -> EventProcessingResult:
        procesados = 0
        errores = 0
        attempted_ids: set[int] = set()
        for _ in range(limit):
            evento = self.eventos.get_next_pending(for_update=True, exclude_ids=attempted_ids)
            if evento is None:
                break
            attempted_ids.add(evento.id)
            try:
                evento.estado = EventoPendienteEstado.PROCESANDO
                evento.intentos += 1
                self.db.commit()
                self._procesar_evento(evento)
                evento.estado = EventoPendienteEstado.PROCESADO
                evento.processed_at = datetime.now()
                evento.ultimo_error = None
                self.db.commit()
                procesados += 1
            except Exception as error:
                self.db.rollback()
                evento = self.eventos.get(evento.id)
                if evento is not None:
                    evento.estado = EventoPendienteEstado.ERROR
                    evento.ultimo_error = str(error)
                    self.db.commit()
                errores += 1
        return EventProcessingResult(procesados=procesados, errores=errores)

    def _procesar_evento(self, evento: EventoPendiente) -> None:
        if evento.tipo != VENTA_FINALIZADA:
            raise ValidationError(f"Tipo de evento no soportado: {evento.tipo}")
        venta_id = int(evento.payload["venta_id"])
        venta = self.get(venta_id)
        for detalle in venta.detalles:
            quantity = int(detalle.cantidad)
            InventoryService(self.db).registrar_movimiento(
                MovimientoStockCreate(
                    global_id=sale_movement_global_id(venta.global_id, detalle.id),
                    variante_id=detalle.variante_id,
                    destino_id=venta.destino_id,
                    estado=StockEstado.DISPONIBLE,
                    cantidad=-quantity,
                    tipo=MovimientoStockTipo.VENTA,
                    origen_tipo="VENTA",
                    origen_id=venta.global_id,
                    referencia=venta.numero_venta,
                )
            )

    def _get_editable_sale(self, venta_id: int) -> Venta:
        venta = self.get(venta_id)
        if venta.estado == VentaEstado.CERRADA:
            raise BusinessRuleViolation(
                "SALE_ALREADY_CLOSED",
                "La venta ya esta cerrada.",
                RuleStatus.DENIED,
            )
        if venta.estado == VentaEstado.ANULADA:
            raise ValidationError("La venta esta anulada")
        return venta

    def _ensure_can_finalize(self, venta: Venta) -> None:
        blocking = self.policy.first_blocking_result(
            SaleFinalizationContext(
                venta_exists=True,
                estado=venta.estado,
                lines=[
                    SaleLineContext(
                        detalle_id=item.id,
                        cantidad=item.cantidad,
                        precio_unitario=item.precio_unitario,
                        importe=item.importe,
                    )
                    for item in venta.detalles
                ],
                subtotal=venta.subtotal,
                total=venta.total,
                pagos_total=sum((pago.importe for pago in venta.pagos), Decimal("0.00")).quantize(MONEY),
            )
        )
        if blocking is not None:
            raise BusinessRuleViolation(blocking.code, blocking.message, blocking.status, blocking.data)

    def _recalculate_totals(self, venta: Venta) -> None:
        self.db.flush()
        total = self.db.scalar(
            select(func.coalesce(func.sum(DetalleVenta.importe), Decimal("0.00"))).where(
                DetalleVenta.venta_id == venta.id
            )
        )
        total = Decimal(total or Decimal("0.00")).quantize(MONEY)
        venta.subtotal = total
        venta.total = total
        self.db.flush()

    def _quantize_money(self, value: Decimal) -> Decimal:
        return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)

    def _quantize_qty(self, value: Decimal) -> Decimal:
        return Decimal(value).quantize(QTY, rounding=ROUND_HALF_UP)


def sale_movement_global_id(venta_global_id: str, detalle_id: int) -> str:
    source = f"sale:{venta_global_id}:line:{detalle_id}"
    return str(uuid5(SALE_MOVEMENT_NAMESPACE, source))
