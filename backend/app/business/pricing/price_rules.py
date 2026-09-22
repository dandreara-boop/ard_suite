from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from backend.app.models.pricing import TipoRedondeoPrecio, TipoReglaPrecio
from backend.app.services.exceptions import ValidationError

PRICE_CONDITION_NOT_FOUND = "PRICE_CONDITION_NOT_FOUND"
PRICE_CONDITION_INACTIVE = "PRICE_CONDITION_INACTIVE"
PRICE_BASE_REQUIRED = "PRICE_BASE_REQUIRED"
PRICE_RULE_REQUIRED = "PRICE_RULE_REQUIRED"
PRICE_RULE_INVALID = "PRICE_RULE_INVALID"
PRICE_PERCENTAGE_INVALID = "PRICE_PERCENTAGE_INVALID"
PRICE_ROUNDING_INVALID = "PRICE_ROUNDING_INVALID"
PRICE_CIRCULAR_DEPENDENCY = "PRICE_CIRCULAR_DEPENDENCY"
ARTICLE_PRICE_NOT_FOUND = "ARTICLE_PRICE_NOT_FOUND"
ARTICLE_PRICE_INVALID = "ARTICLE_PRICE_INVALID"
PRICE_MANUAL_OVERRIDE_INVALID = "PRICE_MANUAL_OVERRIDE_INVALID"
PRICE_RECALCULATION_POLICY_REQUIRED = "PRICE_RECALCULATION_POLICY_REQUIRED"

MONEY = Decimal("0.01")
PERCENT = Decimal("100")


class PriceRuleEngine:
    def calculate(
        self,
        *,
        precio_base: Decimal,
        tipo_regla: TipoReglaPrecio,
        porcentaje: Decimal,
        tipo_redondeo: TipoRedondeoPrecio,
        multiplo_redondeo: Decimal | None = None,
    ) -> Decimal:
        base = self._money(precio_base)
        percentage = Decimal(porcentaje)
        if percentage < 0:
            raise ValidationError(PRICE_PERCENTAGE_INVALID)
        factor = percentage / PERCENT
        if tipo_regla == TipoReglaPrecio.INCREMENTO_PORCENTUAL:
            result = base * (Decimal("1") + factor)
        elif tipo_regla == TipoReglaPrecio.DESCUENTO_PORCENTUAL:
            result = base * (Decimal("1") - factor)
        else:
            raise ValidationError(PRICE_RULE_INVALID)
        if result < 0:
            raise ValidationError(ARTICLE_PRICE_INVALID)
        return self.apply_rounding(result, tipo_redondeo, multiplo_redondeo)

    def apply_rounding(
        self,
        value: Decimal,
        tipo_redondeo: TipoRedondeoPrecio,
        multiplo_redondeo: Decimal | None = None,
    ) -> Decimal:
        amount = Decimal(value)
        if tipo_redondeo == TipoRedondeoPrecio.SIN_REDONDEO:
            return self._money(amount)
        if tipo_redondeo == TipoRedondeoPrecio.ENTERO:
            return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(MONEY)
        if tipo_redondeo == TipoRedondeoPrecio.MULTIPLO:
            if multiplo_redondeo is None or Decimal(multiplo_redondeo) <= 0:
                raise ValidationError(PRICE_ROUNDING_INVALID)
            multiple = Decimal(multiplo_redondeo)
            rounded = (amount / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple
            return self._money(rounded)
        raise ValidationError(PRICE_ROUNDING_INVALID)

    def _money(self, value: Decimal) -> Decimal:
        return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)
