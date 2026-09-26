from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, localcontext
from typing import Protocol

import highspy

from backend.app.business.rules import RuleStatus
from backend.app.models.commercial import MedioPago
from backend.app.schemas.commercial import (
    AsignacionComercialRead,
    PagoResolucionRequest,
    ResolucionComercialRead,
    ResultadoMedioPagoRead,
    TipoSolicitudPago,
)

COMMERCIAL_MEDIO_NOT_FOUND = "COMMERCIAL_MEDIO_NOT_FOUND"
COMMERCIAL_MEDIO_INACTIVE = "COMMERCIAL_MEDIO_INACTIVE"
COMMERCIAL_CONDITION_INACTIVE = "COMMERCIAL_CONDITION_INACTIVE"
COMMERCIAL_MISSING_PRICE = "COMMERCIAL_MISSING_PRICE"
COMMERCIAL_SALE_NOT_EDITABLE = "COMMERCIAL_SALE_NOT_EDITABLE"
COMMERCIAL_NO_PAYMENTS = "COMMERCIAL_NO_PAYMENTS"
COMMERCIAL_MULTIPLE_REMAINDERS = "COMMERCIAL_MULTIPLE_REMAINDERS"
COMMERCIAL_INVALID_FIXED_AMOUNT = "COMMERCIAL_INVALID_FIXED_AMOUNT"
COMMERCIAL_UNRESOLVABLE_AMOUNT = "COMMERCIAL_UNRESOLVABLE_AMOUNT"
COMMERCIAL_INVALID_CONFIGURATION = "COMMERCIAL_INVALID_CONFIGURATION"
COMMERCIAL_ROUNDING_INVALID = "COMMERCIAL_ROUNDING_INVALID"

MONEY = Decimal("0.01")
DEFAULT_INCREMENT = Decimal("0.05")
ONE = Decimal("1")
ZERO = Decimal("0")
SOLVER_TOLERANCE = Decimal("0.000001")
# Internal reconstruction keeps more precision than visible money. This value
# is not a commercial rounding increment; buckets are decided only by
# round_money_up(..., Decimal("0.05")) after Decimal reconstruction.
DECIMAL_RECONSTRUCTION_PRECISION = 50


@dataclass
class CommercialUnit:
    detalle_id: int
    articulo_id: int
    variante_id: int
    codigo_articulo: str
    descripcion: str
    unit_index: int
    prices: dict[int, Decimal]
    price_origins: dict[int, str] = field(default_factory=dict)


@dataclass
class PaymentSpec:
    request: PagoResolucionRequest
    medio: MedioPago


@dataclass(frozen=True)
class _SolverPayment:
    index: int
    spec: PaymentSpec
    is_rest: bool
    requested_amount: Decimal | None


@dataclass
class _SolverResult:
    assignments: list[AsignacionComercialRead]
    medium_internal: dict[int, Decimal]
    medium_final: dict[int, Decimal]
    rounding_adjustments: dict[int, Decimal]
    rest_internal_min: Decimal | None
    rest_final_bucket: Decimal | None
    fractional_units: int
    solver_statuses: dict[str, str]
    variable_count: int
    constraint_count: int


class CommercialOptimizer(Protocol):
    def optimize(
        self,
        *,
        venta_id: int,
        units: list[CommercialUnit],
        payments: list[PaymentSpec],
        rounding_increment: Decimal,
    ) -> _SolverResult:
        ...


def round_money_up(value: Decimal, increment: Decimal = DEFAULT_INCREMENT) -> Decimal:
    amount = Decimal(value)
    step = Decimal(increment)
    if step <= 0:
        _raise(COMMERCIAL_ROUNDING_INVALID, "El incremento de redondeo debe ser mayor a cero.")
    rounded = (amount / step).to_integral_value(rounding=ROUND_CEILING) * step
    return rounded.quantize(MONEY)


class HighsCommercialOptimizer:
    """HiGHS adapter for the commercial LP/MILP model.

    All solver values are converted at this boundary. Business validation is
    reconstructed afterwards with Decimal, so HiGHS tolerances cannot silently
    become domain money rules.
    """

    def optimize(
        self,
        *,
        venta_id: int,
        units: list[CommercialUnit],
        payments: list[PaymentSpec],
        rounding_increment: Decimal,
    ) -> _SolverResult:
        solver_payments = _canonical_payments(payments)
        rest = next((payment for payment in solver_payments if payment.is_rest), None)
        fixed = [payment for payment in solver_payments if not payment.is_rest]

        rest_min: Decimal | None = None
        rest_bucket: Decimal | None = None
        statuses: dict[str, str] = {}

        if rest is not None:
            phase_a = self._solve(
                units=units,
                payments=solver_payments,
                fixed=fixed,
                rest=rest,
                objective="REST_INTERNAL",
            )
            statuses["fase_a"] = phase_a.status
            self._ensure_optimal(phase_a.status)
            _phase_a_assignments, phase_a_internal = self._extract_assignments(
                venta_id=venta_id,
                units=units,
                payments=solver_payments,
                variables=phase_a.x_variables,
                model=phase_a.model,
            )
            rest_min = phase_a_internal[rest.spec.medio.id]
            rest_bucket = round_money_up(rest_min, rounding_increment)

        phase_b = self._solve(
            units=units,
            payments=solver_payments,
            fixed=fixed,
            rest=rest,
            objective="FRACTIONAL_UNITS",
            rest_bucket=rest_bucket,
            rest_min=rest_min,
            include_fraction_variables=True,
        )
        statuses["fase_b"] = phase_b.status
        self._ensure_optimal(phase_b.status)
        fractional_limit = int(round(phase_b.objective_value))

        phase_c = self._solve(
            units=units,
            payments=solver_payments,
            fixed=fixed,
            rest=rest,
            objective="CANONICAL",
            rest_bucket=rest_bucket,
            rest_min=rest_min,
            include_fraction_variables=True,
            fractional_limit=fractional_limit,
        )
        statuses["fase_c"] = phase_c.status
        self._ensure_optimal(phase_c.status)

        assignments, medium_internal = self._extract_assignments(
            venta_id=venta_id,
            units=units,
            payments=solver_payments,
            variables=phase_c.x_variables,
            model=phase_c.model,
        )
        medium_final, rounding_adjustments = self._final_amounts(
            payments=solver_payments,
            medium_internal=medium_internal,
            rounding_increment=rounding_increment,
        )
        fractional_units = _count_fractional_units(assignments)
        self._validate_decimal_solution(
            units=units,
            payments=solver_payments,
            assignments=assignments,
            medium_internal=medium_internal,
            medium_final=medium_final,
            fractional_units=fractional_units,
            fractional_limit=fractional_limit,
            rest=rest,
            rest_bucket=rest_bucket,
            rounding_increment=rounding_increment,
        )
        return _SolverResult(
            assignments=assignments,
            medium_internal=medium_internal,
            medium_final=medium_final,
            rounding_adjustments=rounding_adjustments,
            rest_internal_min=rest_min,
            rest_final_bucket=rest_bucket,
            fractional_units=fractional_units,
            solver_statuses=statuses,
            variable_count=phase_c.variable_count,
            constraint_count=phase_c.constraint_count,
        )

    def _solve(
        self,
        *,
        units: list[CommercialUnit],
        payments: list[_SolverPayment],
        fixed: list[_SolverPayment],
        rest: _SolverPayment | None,
        objective: str,
        rest_bucket: Decimal | None = None,
        rest_min: Decimal | None = None,
        include_fraction_variables: bool = False,
        fractional_limit: int | None = None,
    ) -> "_HighsRun":
        model = highspy.Highs()
        model.setOptionValue("output_flag", False)

        x_variables: dict[tuple[int, int], object] = {}
        y_variables: dict[tuple[int, int], object] = {}
        f_variables: dict[int, object] = {}
        constraint_count = 0

        for unit_index, _unit in enumerate(units):
            for payment in payments:
                x_variables[(unit_index, payment.index)] = model.addVariable(
                    lb=0.0,
                    ub=1.0,
                    name=f"x_u{unit_index}_m{payment.index}",
                )
                if include_fraction_variables:
                    y = model.addBinary(name=f"y_u{unit_index}_m{payment.index}")
                    y_variables[(unit_index, payment.index)] = y
                    model.addConstr(x_variables[(unit_index, payment.index)] <= y)
                    constraint_count += 1

        for unit_index, _unit in enumerate(units):
            model.addConstr(sum(x_variables[(unit_index, payment.index)] for payment in payments) == 1.0)
            constraint_count += 1

        for payment in fixed:
            model.addConstr(
                sum(
                    _solver_float(units[unit_index].prices[payment.spec.medio.condicion_comercial_id])
                    * x_variables[(unit_index, payment.index)]
                    for unit_index in range(len(units))
                )
                == _solver_float(payment.requested_amount or ZERO)
            )
            constraint_count += 1

        rest_expr = None
        if rest is not None:
            rest_expr = sum(
                _solver_float(units[unit_index].prices[rest.spec.medio.condicion_comercial_id])
                * x_variables[(unit_index, rest.index)]
                for unit_index in range(len(units))
            )
            if rest_bucket is not None:
                model.addConstr(rest_expr <= _solver_float(rest_bucket))
                constraint_count += 1
                if rest_min is not None:
                    guarded_lower = max(ZERO, rest_min - SOLVER_TOLERANCE)
                    model.addConstr(rest_expr >= _solver_float(guarded_lower))
                    constraint_count += 1

        if include_fraction_variables:
            for unit_index, _unit in enumerate(units):
                fraction_flag = model.addBinary(name=f"frac_u{unit_index}")
                f_variables[unit_index] = fraction_flag
                positives = sum(y_variables[(unit_index, payment.index)] for payment in payments)
                model.addConstr(positives - 1 <= (len(payments) - 1) * fraction_flag)
                constraint_count += 1
            if fractional_limit is not None:
                model.addConstr(sum(f_variables.values()) <= float(fractional_limit))
                constraint_count += 1

        if objective == "REST_INTERNAL":
            model.minimize(rest_expr if rest_expr is not None else 0.0)
        elif objective == "FRACTIONAL_UNITS":
            model.minimize(sum(f_variables.values()) if f_variables else 0.0)
        elif objective == "CANONICAL":
            model.minimize(
                sum(
                    self._canonical_weight(unit_index, payment.index, len(units))
                    * x_variables[(unit_index, payment.index)]
                    for unit_index in range(len(units))
                    for payment in payments
                )
            )
        else:
            raise AssertionError(f"Objetivo desconocido: {objective}")

        return _HighsRun(
            model=model,
            status=_status_name(model),
            objective_value=float(model.getObjectiveValue()),
            x_variables=x_variables,
            variable_count=len(x_variables) + len(y_variables) + len(f_variables),
            constraint_count=constraint_count,
        )

    def _extract_assignments(
        self,
        *,
        venta_id: int,
        units: list[CommercialUnit],
        payments: list[_SolverPayment],
        variables: dict[tuple[int, int], object],
        model: highspy.Highs,
    ) -> tuple[list[AsignacionComercialRead], dict[int, Decimal]]:
        assignments: list[AsignacionComercialRead] = []
        medium_internal = {payment.spec.medio.id: ZERO for payment in payments}
        fractions = self._reconstruct_decimal_fractions(
            units=units,
            payments=payments,
            variables=variables,
            model=model,
        )
        for unit_index, unit in enumerate(units):
            for payment in payments:
                fraction = _snap_solver_noise(fractions[(unit_index, payment.index)])
                if fraction <= ZERO:
                    continue
                condition_id = payment.spec.medio.condicion_comercial_id
                price = unit.prices[condition_id]
                internal_value = _decimal_product(price, fraction)
                medium_internal[payment.spec.medio.id] += internal_value
                assignments.append(
                    AsignacionComercialRead(
                        medio_pago_id=payment.spec.medio.id,
                        medio_pago_codigo=payment.spec.medio.codigo,
                        condicion_comercial_id=condition_id,
                        detalle_id=unit.detalle_id,
                        articulo_id=unit.articulo_id,
                        variante_id=unit.variante_id,
                        cantidad=fraction,
                        fraccion=fraction,
                        precio_origen=price.quantize(MONEY),
                        precio_destino=price.quantize(MONEY),
                        importe_origen=internal_value,
                        importe_calculado=internal_value,
                        importe_final=internal_value,
                        unidad_indice=unit.unit_index,
                        es_fraccion=False,
                    )
                )
        for assignment in assignments:
            unit_assignments = [
                item
                for item in assignments
                if item.detalle_id == assignment.detalle_id and item.unidad_indice == assignment.unidad_indice
            ]
            assignment.es_fraccion = len(unit_assignments) > 1 or assignment.fraccion != ONE
        return _canonical_assignments(assignments), medium_internal

    def _reconstruct_decimal_fractions(
        self,
        *,
        units: list[CommercialUnit],
        payments: list[_SolverPayment],
        variables: dict[tuple[int, int], object],
        model: highspy.Highs,
    ) -> dict[tuple[int, int], Decimal]:
        fractions = {
            (unit_index, payment.index): _snap_solver_noise(
                Decimal(str(model.val(variables[(unit_index, payment.index)])))
            )
            for unit_index in range(len(units))
            for payment in payments
        }
        rest = next((payment for payment in payments if payment.is_rest), None)

        for unit_index in range(len(units)):
            total = sum((fractions[(unit_index, payment.index)] for payment in payments), ZERO)
            if total > ZERO:
                for payment in payments:
                    fractions[(unit_index, payment.index)] = fractions[(unit_index, payment.index)] / total

        # Solver feasibility is numeric; fixed commercial equalities are exact
        # Decimal contracts. When a fixed medium has a single fractional degree
        # of freedom in the active solution, reconstruct that fraction from the
        # original Decimal price and requested amount instead of from the double.
        for payment in [item for item in payments if not item.is_rest]:
            condition_id = payment.spec.medio.condicion_comercial_id
            adjustable: list[int] = []
            fixed_value = ZERO
            for unit_index, unit in enumerate(units):
                fraction = fractions[(unit_index, payment.index)]
                if fraction <= ZERO:
                    continue
                if ZERO < fraction < ONE:
                    adjustable.append(unit_index)
                else:
                    fixed_value += _decimal_product(unit.prices[condition_id], fraction)
            if len(adjustable) != 1:
                continue
            unit_index = adjustable[0]
            price = units[unit_index].prices[condition_id]
            needed = (payment.requested_amount or ZERO) - fixed_value
            if price <= ZERO:
                continue
            exact_fraction = needed / price
            if -SOLVER_TOLERANCE <= exact_fraction <= ONE + SOLVER_TOLERANCE:
                fractions[(unit_index, payment.index)] = _snap_solver_noise(exact_fraction)

        if rest is not None:
            for unit_index in range(len(units)):
                non_rest = sum(
                    (
                        fractions[(unit_index, payment.index)]
                        for payment in payments
                        if not payment.is_rest
                    ),
                    ZERO,
                )
                fractions[(unit_index, rest.index)] = _snap_solver_noise(ONE - non_rest)

        return fractions

    def _final_amounts(
        self,
        *,
        payments: list[_SolverPayment],
        medium_internal: dict[int, Decimal],
        rounding_increment: Decimal,
    ) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
        final: dict[int, Decimal] = {}
        adjustments: dict[int, Decimal] = {}
        for payment in payments:
            medio_id = payment.spec.medio.id
            if payment.is_rest:
                amount = round_money_up(medium_internal[medio_id], rounding_increment)
            else:
                amount = (payment.requested_amount or ZERO).quantize(MONEY)
            final[medio_id] = amount
            adjustments[medio_id] = amount - medium_internal[medio_id]
        return final, adjustments

    def _validate_decimal_solution(
        self,
        *,
        units: list[CommercialUnit],
        payments: list[_SolverPayment],
        assignments: list[AsignacionComercialRead],
        medium_internal: dict[int, Decimal],
        medium_final: dict[int, Decimal],
        fractional_units: int,
        fractional_limit: int,
        rest: _SolverPayment | None,
        rest_bucket: Decimal | None,
        rounding_increment: Decimal,
    ) -> None:
        by_unit: dict[tuple[int, int], list[AsignacionComercialRead]] = {}
        for assignment in assignments:
            if assignment.fraccion < -SOLVER_TOLERANCE:
                _raise(COMMERCIAL_INVALID_CONFIGURATION, "El solver devolvio una fraccion negativa.")
            by_unit.setdefault((assignment.detalle_id, assignment.unidad_indice), []).append(assignment)

        for unit in units:
            total_fraction = sum(
                (
                    assignment.fraccion
                    for assignment in by_unit.get((unit.detalle_id, unit.unit_index), [])
                ),
                ZERO,
            )
            if abs(total_fraction - ONE) > SOLVER_TOLERANCE:
                _raise(COMMERCIAL_INVALID_CONFIGURATION, "La solucion no cubre completamente una unidad.")

        for payment in payments:
            medio_id = payment.spec.medio.id
            if payment.is_rest:
                expected_final = round_money_up(medium_internal[medio_id], rounding_increment)
                if medium_final[medio_id] != expected_final:
                    _raise(COMMERCIAL_INVALID_CONFIGURATION, "El importe RESTO no respeta el redondeo final.")
                if rest_bucket is not None and medium_final[medio_id] != rest_bucket:
                    _raise(COMMERCIAL_INVALID_CONFIGURATION, "El RESTO quedo fuera del bucket optimo.")
            else:
                requested = (payment.requested_amount or ZERO).quantize(MONEY)
                if abs(medium_internal[medio_id] - requested) > SOLVER_TOLERANCE:
                    _raise(COMMERCIAL_UNRESOLVABLE_AMOUNT, "El solver no respeto un importe fijo solicitado.")
                if medium_final[medio_id] != requested:
                    _raise(COMMERCIAL_UNRESOLVABLE_AMOUNT, "El importe fijo final no coincide con la solicitud.")

        if fractional_units > fractional_limit:
            _raise(COMMERCIAL_INVALID_CONFIGURATION, "El solver no respeto el minimo de unidades fraccionadas.")
        if rest is None and set(medium_final) != {payment.spec.medio.id for payment in payments}:
            _raise(COMMERCIAL_INVALID_CONFIGURATION, "La solucion sin RESTO contiene medios inesperados.")

    def _ensure_optimal(self, status: str) -> None:
        if status != "kOptimal":
            _raise(COMMERCIAL_UNRESOLVABLE_AMOUNT, "No se pudo encontrar una distribucion comercial valida.")

    def _canonical_weight(self, unit_index: int, payment_index: int, unit_count: int) -> float:
        return float((payment_index + 1) * (unit_count - unit_index))


@dataclass
class _HighsRun:
    model: highspy.Highs
    status: str
    objective_value: float
    x_variables: dict[tuple[int, int], object]
    variable_count: int
    constraint_count: int


class CommercialResolutionEngine:
    def __init__(
        self,
        rounding_increment: Decimal = DEFAULT_INCREMENT,
        optimizer: CommercialOptimizer | None = None,
    ) -> None:
        self.rounding_increment = Decimal(rounding_increment)
        self.optimizer = optimizer or HighsCommercialOptimizer()

    def resolve(
        self,
        *,
        venta_id: int,
        units: list[CommercialUnit],
        payments: list[PaymentSpec],
    ) -> ResolucionComercialRead:
        self._validate_request(units, payments)
        result = self.optimizer.optimize(
            venta_id=venta_id,
            units=_canonical_units(units),
            payments=payments,
            rounding_increment=self.rounding_increment,
        )
        return self._build_result(venta_id, _canonical_payments(payments), units, result, "HIGHS_LEXICOGRAPHIC_GLOBAL")

    def _validate_request(self, units: list[CommercialUnit], payments: list[PaymentSpec]) -> None:
        if not payments:
            _raise(COMMERCIAL_NO_PAYMENTS, "La solicitud debe incluir al menos un medio de pago.")
        if not units:
            _raise(COMMERCIAL_INVALID_CONFIGURATION, "La venta no tiene items.")
        rest_payments = [payment for payment in payments if payment.request.tipo == TipoSolicitudPago.RESTO]
        if len(rest_payments) > 1:
            _raise(COMMERCIAL_MULTIPLE_REMAINDERS, "Solo se permite un pago RESTO.")
        for payment in payments:
            if payment.request.tipo == TipoSolicitudPago.IMPORTE_FIJO:
                if payment.request.importe is None or Decimal(payment.request.importe) <= ZERO:
                    _raise(COMMERCIAL_INVALID_FIXED_AMOUNT, "El importe fijo debe ser mayor a cero.")
            condition_id = payment.medio.condicion_comercial_id
            for unit in units:
                if condition_id not in unit.prices:
                    _raise(COMMERCIAL_MISSING_PRICE, "Falta precio efectivo para la condicion solicitada.")

    def _build_result(
        self,
        venta_id: int,
        payments: list[_SolverPayment],
        units: list[CommercialUnit],
        result: _SolverResult,
        criterio: str,
    ) -> ResolucionComercialRead:
        payment_results: list[ResultadoMedioPagoRead] = []
        total = ZERO
        for payment in payments:
            medio_id = payment.spec.medio.id
            amount = result.medium_final[medio_id].quantize(MONEY)
            total += amount
            payment_results.append(
                ResultadoMedioPagoRead(
                    medio_pago_id=medio_id,
                    medio_pago_codigo=payment.spec.medio.codigo,
                    condicion_comercial_id=payment.spec.medio.condicion_comercial_id,
                    tipo_solicitud=payment.spec.request.tipo,
                    importe_solicitado=payment.spec.request.importe,
                    importe_final=amount,
                )
            )

        trace = {
            "criterio": criterio,
            "solver": "HiGHS/highspy",
            "highs_version": highspy.Highs().version(),
            "incremento_redondeo": str(self.rounding_increment),
            "tolerancia_decimal": str(SOLVER_TOLERANCE),
            "resto_minimo_interno": str(result.rest_internal_min) if result.rest_internal_min is not None else None,
            "resto_bucket_final": str(result.rest_final_bucket) if result.rest_final_bucket is not None else None,
            "unidades_fraccionadas": result.fractional_units,
            "solver_statuses": result.solver_statuses,
            "modelo": {
                "variables": result.variable_count,
                "restricciones": result.constraint_count,
                "prioridades": [
                    "menor_total_final",
                    "menor_cantidad_unidades_fraccionadas",
                    "desempate_canonico_determinista",
                ],
            },
            "solicitud": [
                {
                    "medio_pago_id": payment.spec.medio.id,
                    "medio_pago_codigo": payment.spec.medio.codigo,
                    "condicion_comercial_id": payment.spec.medio.condicion_comercial_id,
                    "tipo": payment.spec.request.tipo.value,
                    "importe": str(payment.spec.request.importe) if payment.spec.request.importe is not None else None,
                    "orden_canonico": payment.index,
                }
                for payment in payments
            ],
            "unidades": [
                {
                    "detalle_id": unit.detalle_id,
                    "articulo_id": unit.articulo_id,
                    "variante_id": unit.variante_id,
                    "unidad_indice": unit.unit_index,
                    "precios": {str(condition_id): str(price) for condition_id, price in unit.prices.items()},
                    "origenes": {str(condition_id): origin for condition_id, origin in unit.price_origins.items()},
                }
                for unit in _canonical_units(units)
            ],
            "resultados_por_medio": [
                {
                    **payment_result.model_dump(mode="json"),
                    "subtotal_interno": str(result.medium_internal[payment_result.medio_pago_id]),
                    "ajuste_redondeo": str(result.rounding_adjustments[payment_result.medio_pago_id]),
                }
                for payment_result in payment_results
            ],
            "asignaciones": [
                {
                    **assignment.model_dump(mode="json"),
                    "valor_interno": str(assignment.importe_calculado),
                    "origen_precio": next(
                        (
                            unit.price_origins.get(assignment.condicion_comercial_id)
                            for unit in units
                            if unit.detalle_id == assignment.detalle_id
                            and unit.unit_index == assignment.unidad_indice
                        ),
                        None,
                    ),
                }
                for assignment in result.assignments
            ],
            "total": str(total.quantize(MONEY)),
        }
        return ResolucionComercialRead(
            venta_id=venta_id,
            total=total.quantize(MONEY),
            pagos=payment_results,
            asignaciones=result.assignments,
            traza=trace,
        )


def _canonical_payments(payments: list[PaymentSpec]) -> list[_SolverPayment]:
    fixed = sorted(
        [payment for payment in payments if payment.request.tipo == TipoSolicitudPago.IMPORTE_FIJO],
        key=_payment_key,
    )
    rest = sorted(
        [payment for payment in payments if payment.request.tipo == TipoSolicitudPago.RESTO],
        key=_payment_key,
    )
    ordered = [*fixed, *rest]
    return [
        _SolverPayment(
            index=index,
            spec=payment,
            is_rest=payment.request.tipo == TipoSolicitudPago.RESTO,
            requested_amount=Decimal(payment.request.importe).quantize(MONEY) if payment.request.importe is not None else None,
        )
        for index, payment in enumerate(ordered)
    ]


def _canonical_units(units: list[CommercialUnit]) -> list[CommercialUnit]:
    return sorted(units, key=lambda unit: (unit.detalle_id, unit.unit_index, unit.articulo_id, unit.variante_id))


def _canonical_assignments(assignments: list[AsignacionComercialRead]) -> list[AsignacionComercialRead]:
    return sorted(
        assignments,
        key=lambda item: (
            item.detalle_id,
            item.unidad_indice,
            item.medio_pago_id,
            item.condicion_comercial_id,
            str(item.fraccion),
        ),
    )


def _payment_key(payment: PaymentSpec) -> tuple[int, str]:
    return payment.medio.id or 0, payment.medio.codigo


def _count_fractional_units(assignments: list[AsignacionComercialRead]) -> int:
    by_unit: dict[tuple[int, int], list[AsignacionComercialRead]] = {}
    for assignment in assignments:
        by_unit.setdefault((assignment.detalle_id, assignment.unidad_indice), []).append(assignment)
    return sum(
        1
        for unit_assignments in by_unit.values()
        if len(unit_assignments) > 1 or any(assignment.fraccion != ONE for assignment in unit_assignments)
    )


def _solver_float(value: Decimal) -> float:
    return float(Decimal(value))


def _snap_solver_noise(value: Decimal) -> Decimal:
    if abs(value) <= SOLVER_TOLERANCE:
        return ZERO
    if abs(value - ONE) <= SOLVER_TOLERANCE:
        return ONE
    return value


def _decimal_product(price: Decimal, fraction: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_RECONSTRUCTION_PRECISION
        return Decimal(price) * Decimal(fraction)


def _status_name(model: highspy.Highs) -> str:
    return str(model.getModelStatus()).split(".")[-1]


def _raise(code: str, message: str) -> None:
    from backend.app.services.exceptions import BusinessRuleViolation

    raise BusinessRuleViolation(code, message, RuleStatus.DENIED)
