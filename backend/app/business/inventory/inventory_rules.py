from __future__ import annotations

from dataclasses import dataclass

from backend.app.business.rules.base import RuleInfo
from backend.app.business.rules.result import RuleResult, RuleStatus


INVENTORY_VARIANT_INVALID = "INVENTORY_VARIANT_INVALID"
INVENTORY_DESTINATION_INVALID = "INVENTORY_DESTINATION_INVALID"
INVENTORY_ZERO_MOVEMENT = "INVENTORY_ZERO_MOVEMENT"
INVENTORY_EVENT_ALREADY_PROCESSED = "INVENTORY_EVENT_ALREADY_PROCESSED"
INVENTORY_MOVEMENT_ALLOWED = "INVENTORY_MOVEMENT_ALLOWED"


@dataclass(frozen=True)
class InventoryMovementContext:
    variante_exists: bool
    variante_active: bool
    destino_exists: bool
    destino_active: bool
    cantidad: int
    duplicate_global_id: bool = False


class InventoryVariantActiveRule:
    info = RuleInfo(
        code=INVENTORY_VARIANT_INVALID,
        name="Variante existente y activa",
        description="El movimiento requiere una variante existente y activa.",
    )

    def evaluate(self, context: InventoryMovementContext) -> RuleResult:
        if not context.variante_exists or not context.variante_active:
            return RuleResult.denied(
                self.info.code,
                "La variante no existe o no esta activa.",
            )
        return RuleResult.allowed("INVENTORY_VARIANT_VALID", "La variante es valida.")


class InventoryDestinationActiveRule:
    info = RuleInfo(
        code=INVENTORY_DESTINATION_INVALID,
        name="Destino existente y activo",
        description="El movimiento requiere un destino existente y activo.",
    )

    def evaluate(self, context: InventoryMovementContext) -> RuleResult:
        if not context.destino_exists or not context.destino_active:
            return RuleResult.denied(
                self.info.code,
                "El destino de inventario no existe o no esta activo.",
            )
        return RuleResult.allowed("INVENTORY_DESTINATION_VALID", "El destino es valido.")


class InventoryNonZeroMovementRule:
    info = RuleInfo(
        code=INVENTORY_ZERO_MOVEMENT,
        name="Movimiento distinto de cero",
        description="El movimiento debe cambiar el saldo.",
    )

    def evaluate(self, context: InventoryMovementContext) -> RuleResult:
        if context.cantidad == 0:
            return RuleResult.denied(self.info.code, "La cantidad del movimiento no puede ser cero.")
        return RuleResult.allowed("INVENTORY_NON_ZERO_MOVEMENT", "La cantidad es valida.")


class InventoryIdempotencyRule:
    info = RuleInfo(
        code=INVENTORY_EVENT_ALREADY_PROCESSED,
        name="Evento ya procesado",
        description="Un global_id ya procesado no debe aplicarse nuevamente.",
    )

    def evaluate(self, context: InventoryMovementContext) -> RuleResult:
        if context.duplicate_global_id:
            return RuleResult.denied(self.info.code, "El evento de inventario ya fue procesado.")
        return RuleResult.allowed("INVENTORY_EVENT_NOT_PROCESSED", "El evento no fue procesado antes.")


class InventoryMovementPolicy:
    def __init__(self) -> None:
        self.rules = [
            InventoryIdempotencyRule(),
            InventoryVariantActiveRule(),
            InventoryDestinationActiveRule(),
            InventoryNonZeroMovementRule(),
        ]

    def evaluate(self, context: InventoryMovementContext) -> list[RuleResult]:
        results = [rule.evaluate(context) for rule in self.rules]
        if any(result.status != RuleStatus.ALLOWED for result in results):
            return results
        return [
            *results,
            RuleResult.allowed(
                code=INVENTORY_MOVEMENT_ALLOWED,
                message="El movimiento de inventario puede registrarse.",
            ),
        ]

    def first_blocking_result(self, context: InventoryMovementContext) -> RuleResult | None:
        for result in self.evaluate(context):
            if result.status != RuleStatus.ALLOWED:
                return result
        return None
