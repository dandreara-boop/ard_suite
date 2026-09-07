from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.business.rules.base import RuleInfo
from backend.app.business.rules.result import RuleResult, RuleStatus


VARIANT_CAN_BE_CREATED = "VARIANT_CAN_BE_CREATED"
VARIANT_ATTRIBUTES_NOT_ALLOWED = "VARIANT_ATTRIBUTES_NOT_ALLOWED"
VARIANT_DUPLICATE_ATTRIBUTE = "VARIANT_DUPLICATE_ATTRIBUTE"
VARIANT_ALREADY_EXISTS = "VARIANT_ALREADY_EXISTS"
BARCODE_ALREADY_EXISTS = "BARCODE_ALREADY_EXISTS"


@dataclass(frozen=True)
class VariantValueInput:
    valor_atributo_id: int
    atributo_id: int


@dataclass(frozen=True)
class VariantCreationContext:
    articulo_id: int
    valores: list[VariantValueInput]
    allowed_attribute_ids: set[int]
    existing_variant_value_ids: set[frozenset[int]] = field(default_factory=set)
    codigo_barra: str | None = None
    codigo_barra_existing_variant_id: int | None = None


class VariantAttributesAllowedRule:
    info = RuleInfo(
        code=VARIANT_ATTRIBUTES_NOT_ALLOWED,
        name="Atributos habilitados para variante",
        description="La variante solo puede usar atributos habilitados para el articulo.",
    )

    def evaluate(self, context: VariantCreationContext) -> RuleResult:
        invalid_attribute_ids = sorted(
            {
                value.atributo_id
                for value in context.valores
                if value.atributo_id not in context.allowed_attribute_ids
            }
        )
        if invalid_attribute_ids:
            return RuleResult.denied(
                code=self.info.code,
                message="El valor no corresponde a un atributo habilitado para el articulo",
                data={"atributo_ids": invalid_attribute_ids},
            )
        return RuleResult.allowed(
            code="VARIANT_ATTRIBUTES_ALLOWED",
            message="Los atributos de la variante estan habilitados para el articulo.",
        )


class VariantOneValuePerAttributeRule:
    info = RuleInfo(
        code=VARIANT_DUPLICATE_ATTRIBUTE,
        name="Un valor por atributo",
        description="La variante no puede contener dos valores del mismo atributo.",
    )

    def evaluate(self, context: VariantCreationContext) -> RuleResult:
        seen_attribute_ids: set[int] = set()
        duplicated_attribute_ids: set[int] = set()
        for value in context.valores:
            if value.atributo_id in seen_attribute_ids:
                duplicated_attribute_ids.add(value.atributo_id)
            seen_attribute_ids.add(value.atributo_id)

        if duplicated_attribute_ids:
            return RuleResult.denied(
                code=self.info.code,
                message="La variante no puede tener dos valores del mismo atributo",
                data={"atributo_ids": sorted(duplicated_attribute_ids)},
            )
        return RuleResult.allowed(
            code="VARIANT_ONE_VALUE_PER_ATTRIBUTE",
            message="La variante contiene un unico valor por atributo.",
        )


class VariantDuplicateCombinationRule:
    info = RuleInfo(
        code=VARIANT_ALREADY_EXISTS,
        name="Combinacion de variante unica",
        description="No se puede crear una variante con una combinacion existente.",
    )

    def evaluate(self, context: VariantCreationContext) -> RuleResult:
        value_ids = frozenset(value.valor_atributo_id for value in context.valores)
        if value_ids in context.existing_variant_value_ids:
            return RuleResult.denied(
                code=self.info.code,
                message="Ya existe una variante con esa combinacion",
                data={"valor_atributo_ids": sorted(value_ids)},
            )
        return RuleResult.allowed(
            code="VARIANT_COMBINATION_AVAILABLE",
            message="La combinacion de valores esta disponible.",
        )


class VariantBarcodeUniqueRule:
    info = RuleInfo(
        code=BARCODE_ALREADY_EXISTS,
        name="Codigo de barras unico",
        description="El codigo de barras no puede pertenecer a otra variante.",
    )

    def evaluate(self, context: VariantCreationContext) -> RuleResult:
        if context.codigo_barra_existing_variant_id is not None:
            return RuleResult.denied(
                code=self.info.code,
                message="Ya existe una variante con ese codigo de barras",
                data={
                    "codigo_barra": context.codigo_barra,
                    "variante_id": context.codigo_barra_existing_variant_id,
                },
            )
        return RuleResult.allowed(
            code="BARCODE_AVAILABLE",
            message="El codigo de barras esta disponible.",
        )


class VariantCreationPolicy:
    def __init__(self) -> None:
        self.rules = [
            VariantAttributesAllowedRule(),
            VariantOneValuePerAttributeRule(),
            VariantDuplicateCombinationRule(),
            VariantBarcodeUniqueRule(),
        ]

    def evaluate(self, context: VariantCreationContext) -> list[RuleResult]:
        results = [rule.evaluate(context) for rule in self.rules]
        if any(result.status != RuleStatus.ALLOWED for result in results):
            return results
        return [
            *results,
            RuleResult.allowed(
                code=VARIANT_CAN_BE_CREATED,
                message="La variante puede ser creada.",
            ),
        ]

    def first_blocking_result(self, context: VariantCreationContext) -> RuleResult | None:
        for result in self.evaluate(context):
            if result.status != RuleStatus.ALLOWED:
                return result
        return None
