from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from backend.app.business.commercial.resolution import (
    COMMERCIAL_CONDITION_INACTIVE,
    COMMERCIAL_INVALID_CONFIGURATION,
    COMMERCIAL_MEDIO_INACTIVE,
    COMMERCIAL_MEDIO_NOT_FOUND,
    COMMERCIAL_MISSING_PRICE,
    COMMERCIAL_SALE_NOT_EDITABLE,
    CommercialResolutionEngine,
    CommercialUnit,
    PaymentSpec,
)
from backend.app.business.rules import RuleStatus
from backend.app.models import DetalleVenta, EventoPendiente, PagoVenta, Venta, VentaEstado
from backend.app.models.commercial import MedioPago, ResolucionComercialVenta
from backend.app.models.pricing import CondicionComercialPrecio, PrecioArticulo
from backend.app.repositories.commercial_repository import MedioPagoRepository, ResolucionComercialVentaRepository
from backend.app.repositories.venta_repository import VentaRepository
from backend.app.schemas.commercial import (
    CotizacionLineaRead,
    CotizacionVentaRead,
    MedioPagoCreate,
    MedioPagoUpdate,
    ResolucionComercialRead,
    ResolucionComercialRequest,
)
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, NotFoundError
from backend.app.services.venta_service import VENTA_FINALIZADA

MONEY = Decimal("0.01")


class CommercialService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.medios = MedioPagoRepository(db)
        self.resoluciones = ResolucionComercialVentaRepository(db)
        self.ventas = VentaRepository(db)
        self.engine = CommercialResolutionEngine()

    def create_medio(self, data: MedioPagoCreate) -> MedioPago:
        if self.medios.get_by_codigo(data.codigo):
            raise ConflictError("Ya existe un medio de pago con ese codigo")
        self._ensure_condition(data.condicion_comercial_id, active_required=False)
        medio = MedioPago(**data.model_dump())
        self.medios.add(medio)
        self.db.commit()
        self.db.refresh(medio)
        return medio

    def list_medios(self, active_only: bool = False) -> list[MedioPago]:
        return self.medios.list_ordered(active_only=active_only)

    def get_medio(self, medio_id: int) -> MedioPago:
        medio = self.medios.get(medio_id)
        if medio is None:
            self._raise(COMMERCIAL_MEDIO_NOT_FOUND, "Medio de pago no encontrado.")
        return medio

    def update_medio(self, medio_id: int, data: MedioPagoUpdate) -> MedioPago:
        medio = self.get_medio(medio_id)
        if data.codigo is not None:
            existing = self.medios.get_by_codigo(data.codigo)
            if existing is not None and existing.id != medio.id:
                raise ConflictError("Ya existe un medio de pago con ese codigo")
        if data.condicion_comercial_id is not None:
            self._ensure_condition(data.condicion_comercial_id, active_required=False)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(medio, field, value)
        self.db.commit()
        self.db.refresh(medio)
        return medio

    def cotizar_por_condicion(self, venta_id: int, condicion_id: int) -> CotizacionVentaRead:
        venta = self._get_open_sale(venta_id)
        condicion = self._ensure_condition(condicion_id)
        lines = self._quote_lines(venta, condicion.id)
        return CotizacionVentaRead(
            venta_id=venta.id,
            condicion_comercial_id=condicion.id,
            medio_pago_id=None,
            lineas=lines,
            total=sum((line.subtotal for line in lines), Decimal("0.00")).quantize(MONEY),
        )

    def cotizar_por_medio(self, venta_id: int, medio_id: int) -> CotizacionVentaRead:
        medio = self._ensure_medio(medio_id)
        quote = self.cotizar_por_condicion(venta_id, medio.condicion_comercial_id)
        quote.medio_pago_id = medio.id
        return quote

    def simular_resolucion(self, venta_id: int, data: ResolucionComercialRequest) -> ResolucionComercialRead:
        venta = self._get_open_sale(venta_id)
        payments = self._build_payment_specs(data)
        units = self._build_units(venta, payments)
        return self.engine.resolve(venta_id=venta.id, units=units, payments=payments)

    def confirmar_resolucion(self, venta_id: int, data: ResolucionComercialRequest) -> ResolucionComercialVenta:
        try:
            venta = self.ventas.get_full(venta_id, for_update=True)
            if venta is None:
                raise NotFoundError("Venta no encontrada")
            self._ensure_sale_open(venta)
            if self.resoluciones.get_by_venta(venta.id) is not None:
                self._raise(COMMERCIAL_SALE_NOT_EDITABLE, "La venta ya tiene una resolucion comercial confirmada.")
            resolution = self.simular_resolucion(venta.id, data)
            for pago in list(venta.pagos):
                self.db.delete(pago)
            self.db.flush()
            for result in resolution.pagos:
                self.db.add(
                    PagoVenta(
                        venta_id=venta.id,
                        medio_pago=result.medio_pago_codigo,
                        importe=result.importe_final,
                    )
                )
            venta.subtotal = resolution.total
            venta.total = resolution.total
            venta.estado = VentaEstado.CERRADA
            from datetime import datetime

            venta.cerrada_at = datetime.now()
            snapshot = ResolucionComercialVenta(
                venta_id=venta.id,
                total=resolution.total,
                incremento_redondeo=self.engine.rounding_increment,
                solicitud={"pagos": [payment.model_dump(mode="json") for payment in data.pagos]},
                resultado=resolution.model_dump(mode="json"),
                traza=resolution.traza,
            )
            self.db.add(snapshot)
            self.db.add(
                EventoPendiente(
                    tipo=VENTA_FINALIZADA,
                    aggregate_type="VENTA",
                    aggregate_id=venta.global_id,
                    payload={
                        "venta_id": venta.id,
                        "venta_global_id": venta.global_id,
                        "destino_id": venta.destino_id,
                    },
                )
            )
            self.db.commit()
            self.db.refresh(snapshot)
            return snapshot
        except Exception:
            self.db.rollback()
            raise

    def get_resolucion(self, venta_id: int) -> ResolucionComercialVenta:
        snapshot = self.resoluciones.get_by_venta(venta_id)
        if snapshot is None:
            raise NotFoundError("Resolucion comercial no encontrada")
        return snapshot

    def _quote_lines(self, venta: Venta, condicion_id: int) -> list[CotizacionLineaRead]:
        lines: list[CotizacionLineaRead] = []
        for detalle in venta.detalles:
            articulo_id = detalle.variante.articulo_id
            price = self._get_price(articulo_id, condicion_id)
            quantity = Decimal(detalle.cantidad)
            lines.append(
                CotizacionLineaRead(
                    detalle_id=detalle.id,
                    articulo_id=articulo_id,
                    variante_id=detalle.variante_id,
                    codigo_articulo=detalle.codigo_articulo,
                    descripcion=detalle.descripcion,
                    cantidad=quantity,
                    precio_unitario=price.precio,
                    subtotal=(quantity * price.precio).quantize(MONEY, rounding=ROUND_HALF_UP),
                )
            )
        return lines

    def _build_payment_specs(self, data: ResolucionComercialRequest) -> list[PaymentSpec]:
        return [PaymentSpec(request=payment, medio=self._ensure_medio(payment.medio_pago_id)) for payment in data.pagos]

    def _build_units(self, venta: Venta, payments: list[PaymentSpec]) -> list[CommercialUnit]:
        condition_ids = {payment.medio.condicion_comercial_id for payment in payments}
        units: list[CommercialUnit] = []
        for detalle in venta.detalles:
            quantity = Decimal(detalle.cantidad)
            if quantity != quantity.to_integral_value():
                self._raise(COMMERCIAL_INVALID_CONFIGURATION, "La resolucion comercial requiere cantidades enteras.")
            articulo_id = detalle.variante.articulo_id
            price_rows = {condition_id: self._get_price(articulo_id, condition_id) for condition_id in condition_ids}
            prices = {condition_id: price.precio for condition_id, price in price_rows.items()}
            price_origins = {condition_id: price.origen.value for condition_id, price in price_rows.items()}
            for index in range(1, int(quantity) + 1):
                units.append(
                    CommercialUnit(
                        detalle_id=detalle.id,
                        articulo_id=articulo_id,
                        variante_id=detalle.variante_id,
                        codigo_articulo=detalle.codigo_articulo,
                        descripcion=detalle.descripcion,
                        unit_index=index,
                        prices=prices,
                        price_origins=price_origins,
                    )
                )
        if not units:
            self._raise(COMMERCIAL_INVALID_CONFIGURATION, "La venta no tiene items.")
        return units

    def _get_open_sale(self, venta_id: int) -> Venta:
        venta = self.ventas.get_full(venta_id)
        if venta is None:
            raise NotFoundError("Venta no encontrada")
        self._ensure_sale_open(venta)
        return venta

    def _ensure_sale_open(self, venta: Venta) -> None:
        if venta.estado == VentaEstado.CERRADA:
            self._raise(COMMERCIAL_SALE_NOT_EDITABLE, "La venta ya esta cerrada.")
        if venta.estado == VentaEstado.ANULADA:
            self._raise(COMMERCIAL_SALE_NOT_EDITABLE, "La venta esta anulada.")

    def _ensure_medio(self, medio_id: int) -> MedioPago:
        medio = self.get_medio(medio_id)
        if not medio.activo:
            self._raise(COMMERCIAL_MEDIO_INACTIVE, "Medio de pago inactivo.")
        self._ensure_condition(medio.condicion_comercial_id)
        return medio

    def _ensure_condition(self, condicion_id: int, *, active_required: bool = True) -> CondicionComercialPrecio:
        condicion = self.db.get(CondicionComercialPrecio, condicion_id)
        if condicion is None:
            raise NotFoundError("Condicion comercial no encontrada")
        if active_required and not condicion.activa:
            self._raise(COMMERCIAL_CONDITION_INACTIVE, "Condicion comercial inactiva.")
        return condicion

    def _get_price(self, articulo_id: int, condicion_id: int) -> PrecioArticulo:
        price = self.db.query(PrecioArticulo).filter_by(
            articulo_id=articulo_id,
            condicion_comercial_id=condicion_id,
        ).one_or_none()
        if price is None:
            self._raise(COMMERCIAL_MISSING_PRICE, "Falta precio efectivo para la condicion solicitada.")
        return price

    def _raise(self, code: str, message: str) -> None:
        raise BusinessRuleViolation(code, message, RuleStatus.DENIED)
