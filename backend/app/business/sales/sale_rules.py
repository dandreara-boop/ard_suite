from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.app.business.rules.base import RuleInfo
from backend.app.business.rules.result import RuleResult, RuleStatus
from backend.app.models import VentaEstado


SALE_EMPTY = "SALE_EMPTY"
SALE_INVALID_STATE = "SALE_INVALID_STATE"
SALE_INVALID_LINE_QUANTITY = "SALE_INVALID_LINE_QUANTITY"
SALE_INVALID_LINE_PRICE = "SALE_INVALID_LINE_PRICE"
SALE_PAYMENT_INSUFFICIENT = "SALE_PAYMENT_INSUFFICIENT"
SALE_ALREADY_CLOSED = "SALE_ALREADY_CLOSED"
SALE_TOTAL_INCONSISTENT = "SALE_TOTAL_INCONSISTENT"
SALE_CAN_BE_FINALIZED = "SALE_CAN_BE_FINALIZED"


@dataclass(frozen=True)
class SaleLineContext:
    detalle_id: int
    cantidad: Decimal
    precio_unitario: Decimal
    importe: Decimal


@dataclass(frozen=True)
class SaleFinalizationContext:
    venta_exists: bool
    estado: VentaEstado | None
    lines: list[SaleLineContext]
    subtotal: Decimal
    total: Decimal
    pagos_total: Decimal


class SaleExistingLinesRule:
    info = RuleInfo(
        code=SALE_EMPTY,
        name="Venta con detalle",
        description="La venta debe tener al menos una linea.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        if not context.lines:
            return RuleResult.denied(self.info.code, "La venta no tiene items.")
        return RuleResult.allowed("SALE_HAS_LINES", "La venta tiene items.")


class SaleStateRule:
    info = RuleInfo(
        code=SALE_INVALID_STATE,
        name="Estado finalizable",
        description="La venta debe estar en un estado apto para finalizar.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        if context.estado == VentaEstado.CERRADA:
            return RuleResult.denied(SALE_ALREADY_CLOSED, "La venta ya esta cerrada.")
        if context.estado not in {VentaEstado.ABIERTA, VentaEstado.EN_PAGO}:
            return RuleResult.denied(self.info.code, "La venta no esta en estado valido para finalizar.")
        return RuleResult.allowed("SALE_STATE_VALID", "El estado permite finalizar.")


class SaleLineQuantityRule:
    info = RuleInfo(
        code=SALE_INVALID_LINE_QUANTITY,
        name="Cantidad positiva",
        description="Cada item de venta debe tener cantidad positiva.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        invalid_ids = [
            line.detalle_id
            for line in context.lines
            if line.cantidad <= 0 or line.cantidad != line.cantidad.to_integral_value()
        ]
        if invalid_ids:
            return RuleResult.denied(self.info.code, "Hay items con cantidad invalida.", {"detalle_ids": invalid_ids})
        return RuleResult.allowed("SALE_LINE_QUANTITY_VALID", "Las cantidades son validas.")


class SaleLinePriceRule:
    info = RuleInfo(
        code=SALE_INVALID_LINE_PRICE,
        name="Precio valido",
        description="Cada item de venta debe tener precio positivo.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        invalid_ids = [line.detalle_id for line in context.lines if line.precio_unitario <= 0 or line.importe < 0]
        if invalid_ids:
            return RuleResult.denied(self.info.code, "Hay items con precio invalido.", {"detalle_ids": invalid_ids})
        return RuleResult.allowed("SALE_LINE_PRICE_VALID", "Los precios son validos.")


class SaleTotalConsistentRule:
    info = RuleInfo(
        code=SALE_TOTAL_INCONSISTENT,
        name="Total consistente",
        description="El total debe coincidir con la suma del detalle.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        calculated = sum((line.importe for line in context.lines), Decimal("0.00")).quantize(Decimal("0.01"))
        if context.subtotal != calculated or context.total != calculated:
            return RuleResult.denied(
                self.info.code,
                "El total de la venta no coincide con el detalle.",
                {"subtotal": str(context.subtotal), "total": str(context.total), "calculado": str(calculated)},
            )
        return RuleResult.allowed("SALE_TOTAL_VALID", "El total coincide con el detalle.")


class SalePaymentSufficientRule:
    info = RuleInfo(
        code=SALE_PAYMENT_INSUFFICIENT,
        name="Pagos suficientes",
        description="Los pagos deben cubrir el total de la venta.",
    )

    def evaluate(self, context: SaleFinalizationContext) -> RuleResult:
        if context.pagos_total < context.total:
            return RuleResult.denied(
                self.info.code,
                "Los pagos no cubren el total de la venta.",
                {"pagos": str(context.pagos_total), "total": str(context.total)},
            )
        return RuleResult.allowed("SALE_PAYMENT_SUFFICIENT", "Los pagos cubren el total.")


class SaleFinalizationPolicy:
    def __init__(self) -> None:
        self.rules = [
            SaleStateRule(),
            SaleExistingLinesRule(),
            SaleLineQuantityRule(),
            SaleLinePriceRule(),
            SaleTotalConsistentRule(),
            SalePaymentSufficientRule(),
        ]

    def evaluate(self, context: SaleFinalizationContext) -> list[RuleResult]:
        results = [rule.evaluate(context) for rule in self.rules]
        if any(result.status != RuleStatus.ALLOWED for result in results):
            return results
        return [*results, RuleResult.allowed(SALE_CAN_BE_FINALIZED, "La venta puede finalizarse.")]

    def first_blocking_result(self, context: SaleFinalizationContext) -> RuleResult | None:
        for result in self.evaluate(context):
            if result.status != RuleStatus.ALLOWED:
                return result
        return None
