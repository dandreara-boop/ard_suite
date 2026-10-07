from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.business.rules import RuleStatus
from backend.app.business.sales import SaleFinalizationContext, SaleFinalizationPolicy
from backend.app.business.sales.sale_rules import SaleLineContext
from backend.app.models import (
    DetalleVenta,
    DestinoInventario,
    EventoOperacionVenta,
    EventoOperacionVentaTipo,
    EventoPendiente,
    EventoPendienteEstado,
    MovimientoStockTipo,
    PagoVenta,
    SecuenciaNumeroCortoVenta,
    SesionCaja,
    SesionCajaEstado,
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
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate, VentaUpdate
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, NotFoundError, ValidationError
from backend.app.services.inventory_service import InventoryService

VENTA_FINALIZADA = "VENTA_FINALIZADA"
SALE_ALREADY_CAPTURED = "SALE_ALREADY_CAPTURED"
SALE_NOT_EDITABLE = "SALE_NOT_EDITABLE"
SALE_INVALID_OPERATION_STATE = "SALE_INVALID_OPERATION_STATE"
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
            referencia_cliente=data.referencia_cliente,
            vendedor_id=data.vendedor_id,
            tipo_atencion=data.tipo_atencion,
        )
        try:
            self.db.add(venta)
            self.db.flush()
            venta.numero_venta = f"V-{venta.id:08d}"
            self._registrar_evento_operacion(
                venta,
                EventoOperacionVentaTipo.CREACION,
                usuario_id=data.usuario_id,
                payload={"estado": venta.estado.value},
            )
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def update_preparacion(self, venta_id: int, data: VentaUpdate) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            self._ensure_preparation_editable(venta)
            changes = data.model_dump(exclude_unset=True)
            for field, value in changes.items():
                setattr(venta, field, value)
            if changes:
                self._registrar_evento_operacion(
                    venta,
                    EventoOperacionVentaTipo.MODIFICACION,
                    usuario_id=venta.usuario_id,
                    payload={"campos": sorted(changes), "estado": venta.estado.value},
                )
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

    def finalizar(self, venta_id: int, *, usuario_id: int | None = None) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            self._recalculate_totals(venta)
            self._ensure_can_finalize(venta)
            if venta.estado == VentaEstado.EN_COBRO:
                self._validate_sale_session_for_confirmation(venta, usuario_id=usuario_id)
            venta.estado = VentaEstado.CERRADA
            venta.cerrada_at = datetime.now()
            self._registrar_evento_operacion(
                venta,
                EventoOperacionVentaTipo.CONFIRMACION,
                usuario_id=usuario_id if usuario_id is not None else venta.usuario_id,
                caja_id=venta.caja_captura_id,
                sesion_caja_id=venta.sesion_caja_id,
                payload={"estado": VentaEstado.CERRADA.value},
            )
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

    def enviar_a_caja(self, venta_id: int, *, usuario_id: int | None = None) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            if venta.estado != VentaEstado.ABIERTA:
                if venta.estado in {VentaEstado.CERRADA, VentaEstado.ANULADA}:
                    raise ValidationError("La venta cerrada o anulada no puede enviarse a caja")
                raise ConflictError("La venta no esta en estado ABIERTA")
            if venta.numero_corto is None:
                venta.numero_corto = self._next_numero_corto(venta.destino_id)
            venta.estado = VentaEstado.LISTA_PARA_COBRAR
            venta.caja_captura_id = None
            venta.sesion_caja_id = None
            venta.capturada_at = None
            self._registrar_evento_operacion(
                venta,
                EventoOperacionVentaTipo.ENVIO_CAJA,
                usuario_id=usuario_id,
                payload={"numero_corto": venta.numero_corto, "estado": venta.estado.value},
            )
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def listar_pendientes_caja(
        self,
        *,
        destino_id: int,
        numero_corto: int | None = None,
        referencia_cliente: str | None = None,
    ) -> list[Venta]:
        statement = (
            select(Venta)
            .where(Venta.destino_id == destino_id, Venta.estado == VentaEstado.LISTA_PARA_COBRAR)
            .order_by(Venta.numero_corto, Venta.id)
        )
        if numero_corto is not None:
            statement = statement.where(Venta.numero_corto == numero_corto)
        if referencia_cliente is not None:
            statement = statement.where(Venta.referencia_cliente.ilike(f"%{referencia_cliente}%"))
        return list(self.db.scalars(statement).all())

    def capturar(self, venta_id: int, *, sesion_caja_id: int, usuario_id: int) -> Venta:
        sesion = self._get_operable_session(sesion_caja_id, usuario_id=usuario_id)
        caja = sesion.caja
        venta = self.get(venta_id)
        if venta.destino_id != caja.destino_id:
            raise ValidationError("La caja no pertenece al mismo destino de la venta")
        now = datetime.now()
        try:
            result = self.db.execute(
                update(Venta)
                .where(Venta.id == venta_id, Venta.estado == VentaEstado.LISTA_PARA_COBRAR)
                .values(
                    estado=VentaEstado.EN_COBRO,
                    caja_captura_id=caja.id,
                    sesion_caja_id=sesion.id,
                    capturada_at=now,
                )
            )
            if result.rowcount != 1:
                current = self.get(venta_id)
                if current.estado == VentaEstado.EN_COBRO:
                    raise BusinessRuleViolation(
                        SALE_ALREADY_CAPTURED,
                        "La venta ya fue capturada por otra caja.",
                        RuleStatus.DENIED,
                        {
                            "venta_id": venta_id,
                            "caja_captura_id": current.caja_captura_id,
                            "sesion_caja_id": current.sesion_caja_id,
                        },
                    )
                if current.estado in {VentaEstado.CERRADA, VentaEstado.ANULADA}:
                    raise ValidationError("La venta cerrada o anulada no puede capturarse")
                raise ConflictError("La venta no esta lista para cobrar")
            captured = self.db.get(Venta, venta_id)
            if captured is None:
                raise NotFoundError("Venta no encontrada")
            self._registrar_evento_operacion(
                captured,
                EventoOperacionVentaTipo.CAPTURA,
                usuario_id=usuario_id,
                caja_id=caja.id,
                sesion_caja_id=sesion.id,
                payload={"estado": VentaEstado.EN_COBRO.value},
            )
            self.db.commit()
            return self.get(venta_id)
        except Exception:
            self.db.rollback()
            raise

    def liberar(self, venta_id: int, *, usuario_id: int | None = None) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            if venta.estado != VentaEstado.EN_COBRO:
                if venta.estado in {VentaEstado.CERRADA, VentaEstado.ANULADA}:
                    raise ValidationError("La venta cerrada o anulada no puede liberarse")
                raise ConflictError("La venta no esta capturada")
            caja_id = venta.caja_captura_id
            sesion_caja_id = venta.sesion_caja_id
            venta.estado = VentaEstado.LISTA_PARA_COBRAR
            venta.caja_captura_id = None
            venta.sesion_caja_id = None
            venta.capturada_at = None
            self._registrar_evento_operacion(
                venta,
                EventoOperacionVentaTipo.LIBERACION,
                usuario_id=usuario_id,
                caja_id=caja_id,
                sesion_caja_id=sesion_caja_id,
                payload={
                    "estado": venta.estado.value,
                    "numero_corto": venta.numero_corto,
                    "caja_anterior_id": caja_id,
                    "sesion_caja_anterior_id": sesion_caja_id,
                },
            )
            self.db.commit()
            return self.get(venta.id)
        except Exception:
            self.db.rollback()
            raise

    def anular(self, venta_id: int, *, usuario_id: int | None = None) -> Venta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            if venta.estado == VentaEstado.CERRADA:
                raise BusinessRuleViolation(
                    "SALE_ALREADY_CLOSED",
                    "La venta ya esta cerrada.",
                    RuleStatus.DENIED,
                )
            if venta.estado == VentaEstado.ANULADA:
                raise ValidationError("La venta ya esta anulada")
            venta.estado = VentaEstado.ANULADA
            self._registrar_evento_operacion(
                venta,
                EventoOperacionVentaTipo.ANULACION,
                usuario_id=usuario_id,
                caja_id=venta.caja_captura_id,
                payload={"numero_corto": venta.numero_corto, "estado": venta.estado.value},
            )
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
        if venta.estado == VentaEstado.LISTA_PARA_COBRAR:
            raise BusinessRuleViolation(
                SALE_NOT_EDITABLE,
                "La venta esta esperando caja y no puede modificarse.",
                RuleStatus.DENIED,
            )
        if venta.estado not in {VentaEstado.ABIERTA, VentaEstado.EN_COBRO, VentaEstado.EN_PAGO}:
            raise BusinessRuleViolation(
                SALE_INVALID_OPERATION_STATE,
                "La venta no esta en estado editable.",
                RuleStatus.DENIED,
            )
        return venta

    def _ensure_preparation_editable(self, venta: Venta) -> None:
        if venta.estado != VentaEstado.ABIERTA:
            if venta.estado in {VentaEstado.CERRADA, VentaEstado.ANULADA}:
                raise ValidationError("La venta cerrada o anulada no puede modificarse")
            raise BusinessRuleViolation(
                SALE_NOT_EDITABLE,
                "La venta no esta en preparacion editable.",
                RuleStatus.DENIED,
            )

    def _next_numero_corto(self, destino_id: int) -> int:
        self._initialize_short_number_sequence(destino_id)
        sequence = self.db.scalar(
            select(SecuenciaNumeroCortoVenta)
            .where(SecuenciaNumeroCortoVenta.destino_id == destino_id)
            .with_for_update()
        )
        if sequence is None:
            raise RuntimeError("No se pudo inicializar la secuencia de numero corto")
        sequence.ultimo_numero += 1
        self.db.flush()
        return sequence.ultimo_numero

    def _initialize_short_number_sequence(self, destino_id: int) -> None:
        values = {"destino_id": destino_id, "ultimo_numero": 0}
        dialect_name = self.db.bind.dialect.name if self.db.bind is not None else ""
        if dialect_name in {"mysql", "mariadb"}:
            statement = mysql_insert(SecuenciaNumeroCortoVenta).values(**values).prefix_with("IGNORE")
        elif dialect_name == "sqlite":
            statement = (
                sqlite_insert(SecuenciaNumeroCortoVenta)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["destino_id"])
            )
        else:
            statement = mysql_insert(SecuenciaNumeroCortoVenta).values(**values).prefix_with("IGNORE")
        self.db.execute(statement)
        self.db.flush()

    def _registrar_evento_operacion(
        self,
        venta: Venta,
        tipo: EventoOperacionVentaTipo,
        *,
        usuario_id: int | None = None,
        caja_id: int | None = None,
        sesion_caja_id: int | None = None,
        payload: dict | None = None,
    ) -> EventoOperacionVenta:
        event = EventoOperacionVenta(
            venta_id=venta.id,
            tipo=tipo,
            usuario_id=usuario_id,
            caja_id=caja_id,
            sesion_caja_id=sesion_caja_id if sesion_caja_id is not None else venta.sesion_caja_id,
            payload=payload or {},
        )
        self.db.add(event)
        return event

    def _get_operable_session(self, sesion_caja_id: int, *, usuario_id: int) -> SesionCaja:
        sesion = self.db.scalar(
            select(SesionCaja).where(SesionCaja.id == sesion_caja_id).with_for_update()
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

    def _validate_sale_session_for_confirmation(self, venta: Venta, *, usuario_id: int | None = None) -> None:
        if usuario_id is None:
            raise ValidationError("La confirmacion de una venta capturada requiere usuario_id")
        if venta.sesion_caja_id is None:
            raise ValidationError("La venta capturada no tiene sesion de caja")
        sesion = self.db.scalar(
            select(SesionCaja).where(SesionCaja.id == venta.sesion_caja_id).with_for_update()
        )
        if sesion is None:
            raise NotFoundError("Sesion de caja no encontrada")
        if sesion.estado != SesionCajaEstado.ABIERTA:
            raise ValidationError("La sesion de caja no esta abierta")
        if venta.caja_captura_id != sesion.caja_id:
            raise ValidationError("La sesion no corresponde a la caja de captura de la venta")
        if sesion.cajero_id != usuario_id:
            raise ValidationError("La sesion de caja pertenece a otro cajero")

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
