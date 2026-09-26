from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from fractions import Fraction
from itertools import combinations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.business.commercial import COMMERCIAL_MULTIPLE_REMAINDERS, round_money_up
from backend.app.business.commercial.resolution import CommercialResolutionEngine, CommercialUnit, PaymentSpec
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    Articulo,
    Categoria,
    DestinoInventario,
    DestinoInventarioTipo,
    EventoPendiente,
    MovimientoStock,
    StockActual,
    Variante,
    VentaEstado,
)
from backend.app.models.commercial import MedioPago, ResolucionComercialVenta
from backend.app.models.pricing import CondicionPrecioTipo, TipoRedondeoPrecio, TipoReglaPrecio
from backend.app.schemas.commercial import (
    MedioPagoCreate,
    MedioPagoUpdate,
    PagoResolucionRequest,
    ResolucionComercialRequest,
    TipoSolicitudPago,
)
from backend.app.schemas.pricing import CondicionComercialPrecioCreate, PrecioBaseArticuloRequest, PrecioManualArticuloRequest
from backend.app.schemas.venta import DetalleVentaCreate, VentaCreate
from backend.app.services.commercial_service import CommercialService
from backend.app.services.exceptions import BusinessRuleViolation
from backend.app.services.inventory_service import InventoryService
from backend.app.services.pricing_service import PricingService
from backend.app.services.venta_service import VentaService


def engine_medio(medio_id: int, codigo: str, condicion_id: int) -> MedioPago:
    return MedioPago(id=medio_id, codigo=codigo, nombre=codigo, condicion_comercial_id=condicion_id, activo=True)


def engine_payment(medio: MedioPago, amount: str | None = None) -> PaymentSpec:
    request = payment(medio, amount)
    return PaymentSpec(request=request, medio=medio)


def engine_unit(unit_id: int, prices: dict[int, str]) -> CommercialUnit:
    return CommercialUnit(
        detalle_id=unit_id,
        articulo_id=unit_id,
        variante_id=unit_id,
        codigo_articulo=f"U{unit_id}",
        descripcion=f"Unidad {unit_id}",
        unit_index=1,
        prices={condition_id: Decimal(price) for condition_id, price in prices.items()},
    )


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def setup_catalog(db: Session) -> dict[str, object]:
    categoria = Categoria(nombre="Comercial")
    destino = DestinoInventario(codigo="LOCAL", nombre="Local", tipo=DestinoInventarioTipo.LOCAL)
    db.add_all([categoria, destino])
    db.commit()
    pricing = PricingService(db)
    base = pricing.create_condicion(
        CondicionComercialPrecioCreate(codigo="PRECIO_1", nombre="Contado", tipo=CondicionPrecioTipo.BASE)
    )
    visa = pricing.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="PRECIO_2",
            nombre="Visa",
            tipo=CondicionPrecioTipo.DERIVADA,
            condicion_base_id=base.id,
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("10"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
        )
    )
    qr = pricing.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="PRECIO_3",
            nombre="QR",
            tipo=CondicionPrecioTipo.DERIVADA,
            condicion_base_id=base.id,
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("20"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
        )
    )
    commercial = CommercialService(db)
    efectivo = commercial.create_medio(MedioPagoCreate(codigo="EFECTIVO", nombre="Efectivo", condicion_comercial_id=base.id))
    transferencia = commercial.create_medio(
        MedioPagoCreate(codigo="TRANSFERENCIA", nombre="Transferencia", condicion_comercial_id=base.id)
    )
    medio_visa = commercial.create_medio(MedioPagoCreate(codigo="VISA", nombre="Visa", condicion_comercial_id=visa.id))
    medio_qr = commercial.create_medio(MedioPagoCreate(codigo="QR", nombre="QR", condicion_comercial_id=qr.id))
    return {
        "categoria": categoria,
        "destino": destino,
        "base": base,
        "visa": visa,
        "qr": qr,
        "efectivo": efectivo,
        "transferencia": transferencia,
        "medio_visa": medio_visa,
        "medio_qr": medio_qr,
    }


def add_article_with_prices(
    db: Session,
    ctx: dict[str, object],
    code: str,
    base_price: str,
    visa_price: str | None = None,
    qr_price: str | None = None,
) -> Variante:
    articulo = Articulo(codigo=code, nombre=f"Articulo {code}", categoria=ctx["categoria"])
    variante = Variante(articulo=articulo, codigo_barra=f"779{code[-6:].zfill(6)}")
    db.add_all([articulo, variante])
    db.commit()
    pricing = PricingService(db)
    pricing.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal(base_price)))
    if visa_price is not None:
        pricing.set_precio_manual(
            articulo.id,
            PrecioManualArticuloRequest(condicion_comercial_id=ctx["visa"].id, precio=Decimal(visa_price)),  # type: ignore[union-attr]
        )
    if qr_price is not None:
        pricing.set_precio_manual(
            articulo.id,
            PrecioManualArticuloRequest(condicion_comercial_id=ctx["qr"].id, precio=Decimal(qr_price)),  # type: ignore[union-attr]
        )
    return variante


def create_sale(db: Session, ctx: dict[str, object], items: list[tuple[Variante, str]]) -> int:
    service = VentaService(db)
    venta = service.create(VentaCreate(destino_id=ctx["destino"].id))  # type: ignore[union-attr]
    for variante, quantity in items:
        venta = service.add_item(
            venta.id,
            DetalleVentaCreate(variante_id=variante.id, cantidad=Decimal(quantity), precio_unitario=Decimal("1.00")),
        )
    return venta.id


def payment(medio: MedioPago, amount: str | None = None) -> PagoResolucionRequest:
    if amount is None:
        return PagoResolucionRequest(medio_pago_id=medio.id, tipo=TipoSolicitudPago.RESTO)
    return PagoResolucionRequest(medio_pago_id=medio.id, tipo=TipoSolicitudPago.IMPORTE_FIJO, importe=Decimal(amount))


def test_crear_medios_y_compartir_condicion(db_session: Session) -> None:
    ctx = setup_catalog(db_session)

    assert ctx["efectivo"].condicion_comercial_id == ctx["base"].id  # type: ignore[union-attr]
    assert ctx["transferencia"].condicion_comercial_id == ctx["base"].id  # type: ignore[union-attr]
    assert ctx["medio_visa"].condicion_comercial_id == ctx["visa"].id  # type: ignore[union-attr]


def test_cotizacion_base_y_visa_no_modifica_venta(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-BASE-VISA", "10000", "11500")
    venta_id = create_sale(db_session, ctx, [(variante, "2")])
    venta_before = VentaService(db_session).get(venta_id)

    base_quote = CommercialService(db_session).cotizar_por_condicion(venta_id, ctx["base"].id)  # type: ignore[union-attr]
    visa_quote = CommercialService(db_session).cotizar_por_medio(venta_id, ctx["medio_visa"].id)  # type: ignore[union-attr]
    venta_after = VentaService(db_session).get(venta_id)

    assert base_quote.total == Decimal("20000.00")
    assert visa_quote.total == Decimal("23000.00")
    assert venta_after.estado == VentaEstado.ABIERTA
    assert venta_after.total == venta_before.total
    assert venta_after.pagos == []


def test_pago_simple_con_un_medio(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-SIMPLE", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "1")])

    resolution = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )

    assert resolution.total == Decimal("11000.00")
    assert resolution.pagos[0].medio_pago_codigo == "VISA"


def test_mixto_efectivo_40000_resto_visa_y_conversion_inversa(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    v1 = add_article_with_prices(db_session, ctx, "ART-MIX-A", "30000", "33000")
    v2 = add_article_with_prices(db_session, ctx, "ART-MIX-B", "30000", "33000")
    venta_id = create_sale(db_session, ctx, [(v1, "1"), (v2, "1")])

    forward = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["efectivo"], "40000"), payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )
    reverse = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"], "40000"), payment(ctx["efectivo"])]),  # type: ignore[arg-type]
    )

    assert forward.total == Decimal("62000.00")
    assert any(item.es_fraccion for item in forward.asignaciones)
    assert reverse.total == Decimal("63636.40")


def test_tres_y_mas_de_tres_medios(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    v1 = add_article_with_prices(db_session, ctx, "ART-3M-A", "10000", "11000", "12000")
    v2 = add_article_with_prices(db_session, ctx, "ART-3M-B", "20000", "23000", "25000")
    venta_id = create_sale(db_session, ctx, [(v1, "1"), (v2, "1")])

    three = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(
            pagos=[
                payment(ctx["efectivo"], "10000"),  # type: ignore[arg-type]
                payment(ctx["medio_qr"], "12000"),  # type: ignore[arg-type]
                payment(ctx["medio_visa"]),  # type: ignore[arg-type]
            ]
        ),
    )
    more_than_three = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(
            pagos=[
                payment(ctx["efectivo"], "5000"),  # type: ignore[arg-type]
                payment(ctx["transferencia"], "5000"),  # type: ignore[arg-type]
                payment(ctx["medio_qr"], "12000"),  # type: ignore[arg-type]
                payment(ctx["medio_visa"]),  # type: ignore[arg-type]
            ]
        ),
    )

    assert len(three.pagos) == 3
    assert len(more_than_three.pagos) == 4


def test_rechazar_dos_resto(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-2REST", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "1")])

    with pytest.raises(BusinessRuleViolation) as error:
        CommercialService(db_session).simular_resolucion(
            venta_id,
            ResolucionComercialRequest(pagos=[payment(ctx["efectivo"]), payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
        )

    assert error.value.code == COMMERCIAL_MULTIPLE_REMAINDERS


def test_varias_unidades_prioriza_unidades_completas_y_determinismo(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-UNITS", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "3")])
    request = ResolucionComercialRequest(pagos=[payment(ctx["efectivo"], "20000"), payment(ctx["medio_visa"])])  # type: ignore[arg-type]

    first = CommercialService(db_session).simular_resolucion(venta_id, request)
    second = CommercialService(db_session).simular_resolucion(venta_id, request)

    assert first.total == Decimal("31000.00")
    assert not any(item.es_fraccion for item in first.asignaciones)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_optimizacion_abc_total_64000_no_66000(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    a = add_article_with_prices(db_session, ctx, "ART-OPT-A", "10000", "11000")
    b = add_article_with_prices(db_session, ctx, "ART-OPT-B", "20000", "23000")
    c = add_article_with_prices(db_session, ctx, "ART-OPT-C", "30000", "36000")
    venta_id = create_sale(db_session, ctx, [(a, "1"), (b, "1"), (c, "1")])

    result = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["efectivo"], "30000"), payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )

    cash_codes = {
        item.articulo_id
        for item in result.asignaciones
        if item.medio_pago_codigo == "EFECTIVO" and item.importe_final == Decimal("30000.00")
    }
    assert result.total == Decimal("64000.00")
    assert len(cash_codes) == 1


def test_conversion_entre_condiciones_no_base_y_precio_manual(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-NONBASE", "10000", "10777", "13000")
    venta_id = create_sale(db_session, ctx, [(variante, "2")])

    result = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"], "10777"), payment(ctx["medio_qr"])]),  # type: ignore[arg-type]
    )

    assert result.total == Decimal("23777.00")
    assert result.pagos[0].importe_final == Decimal("10777.00")
    assert result.pagos[1].importe_final == Decimal("13000.00")


def test_redondeo_monetario_hacia_arriba() -> None:
    assert round_money_up(Decimal("10.00")) == Decimal("10.00")
    assert round_money_up(Decimal("10.01")) == Decimal("10.05")
    assert round_money_up(Decimal("10.03")) == Decimal("10.05")
    assert round_money_up(Decimal("10.05")) == Decimal("10.05")
    assert round_money_up(Decimal("10.051")) == Decimal("10.10")


def test_traza_estructurada_coherente(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-TRACE", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "1")])

    result = CommercialService(db_session).simular_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["efectivo"], "5000"), payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )

    assert result.traza["criterio"] == "HIGHS_LEXICOGRAPHIC_GLOBAL"
    assert result.traza["solver"] == "HiGHS/highspy"
    assert result.traza["asignaciones"]
    assert result.traza["resultados_por_medio"]


def test_contraejemplo_global_ab_y_ba_mismo_optimo_y_distribucion() -> None:
    medio_a = engine_medio(1, "A", 1)
    medio_b = engine_medio(2, "B", 2)
    resto = engine_medio(3, "R", 3)
    units = [
        engine_unit(1, {1: "10", 2: "10", 3: "100"}),
        engine_unit(2, {1: "10", 2: "100", 3: "99"}),
        engine_unit(3, {1: "100", 2: "10", 3: "1"}),
    ]
    engine = CommercialResolutionEngine()

    ab = engine.resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(medio_a, "10"), engine_payment(medio_b, "10"), engine_payment(resto)],
    )
    ba = engine.resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(medio_b, "10"), engine_payment(medio_a, "10"), engine_payment(resto)],
    )

    expected = [("B", 1, Decimal("1")), ("A", 2, Decimal("1")), ("R", 3, Decimal("1"))]
    assert ab.total == Decimal("21.00")
    assert ba.total == Decimal("21.00")
    assert [(item.medio_pago_codigo, item.articulo_id, item.fraccion) for item in ab.asignaciones] == expected
    assert [(item.medio_pago_codigo, item.articulo_id, item.fraccion) for item in ba.asignaciones] == expected


def test_multiples_fijos_sin_resto_cubren_operacion_completa() -> None:
    medio_a = engine_medio(1, "A", 1)
    medio_b = engine_medio(2, "B", 2)
    units = [
        engine_unit(1, {1: "10", 2: "50"}),
        engine_unit(2, {1: "50", 2: "20"}),
    ]

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(medio_a, "10"), engine_payment(medio_b, "20")],
    )

    assert result.total == Decimal("30.00")
    assert [(item.medio_pago_codigo, item.articulo_id) for item in result.asignaciones] == [("A", 1), ("B", 2)]


def test_multiples_fijos_sin_resto_con_saldo_sin_cubrir_rechaza() -> None:
    medio_a = engine_medio(1, "A", 1)
    medio_b = engine_medio(2, "B", 2)
    units = [
        engine_unit(1, {1: "10", 2: "10"}),
        engine_unit(2, {1: "10", 2: "10"}),
    ]

    with pytest.raises(BusinessRuleViolation):
        CommercialResolutionEngine().resolve(
            venta_id=1,
            units=units,
            payments=[engine_payment(medio_a, "10"), engine_payment(medio_b, "5")],
        )


def test_medios_misma_condicion_evitan_fraccionamiento_innecesario() -> None:
    efectivo = engine_medio(1, "EFECTIVO", 1)
    transferencia = engine_medio(2, "TRANSFERENCIA", 1)
    units = [
        engine_unit(1, {1: "10"}),
        engine_unit(2, {1: "10"}),
    ]

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(transferencia, "10"), engine_payment(efectivo, "10")],
    )

    assert result.total == Decimal("20.00")
    assert not any(item.es_fraccion for item in result.asignaciones)
    assert [(item.medio_pago_codigo, item.articulo_id) for item in result.asignaciones] == [
        ("EFECTIVO", 1),
        ("TRANSFERENCIA", 2),
    ]


def test_precios_manuales_no_proporcionales_multi_medio_optimo_global() -> None:
    medio_a = engine_medio(1, "A", 1)
    medio_b = engine_medio(2, "B", 2)
    resto = engine_medio(3, "R", 3)
    units = [
        engine_unit(1, {1: "10", 2: "10", 3: "100"}),
        engine_unit(2, {1: "10", 2: "100", 3: "99"}),
        engine_unit(3, {1: "100", 2: "10", 3: "1"}),
    ]

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(medio_a, "10"), engine_payment(medio_b, "10"), engine_payment(resto)],
    )

    assert result.total == Decimal("21.00")
    assert [(item.medio_pago_codigo, item.articulo_id) for item in result.asignaciones] == [
        ("B", 1),
        ("A", 2),
        ("R", 3),
    ]


def brute_force_full_units(
    units: list[CommercialUnit],
    payments: list[PaymentSpec],
) -> Decimal:
    from itertools import product

    medios = sorted(payments, key=lambda item: (item.medio.id, item.medio.codigo))
    best: Decimal | None = None
    for choices in product(range(len(medios)), repeat=len(units)):
        totals = {payment.medio.id: Decimal("0.00") for payment in medios}
        valid = True
        for unit, choice in zip(units, choices, strict=True):
            payment_spec = medios[choice]
            condition_id = payment_spec.medio.condicion_comercial_id
            totals[payment_spec.medio.id] += round_money_up(unit.prices[condition_id])
        for payment_spec in medios:
            if payment_spec.request.tipo == TipoSolicitudPago.IMPORTE_FIJO:
                requested = round_money_up(Decimal(payment_spec.request.importe or Decimal("0")))
                valid = valid and totals[payment_spec.medio.id] == requested
        if not valid:
            continue
        total = sum(totals.values(), Decimal("0.00"))
        if best is None or total < best:
            best = total
    assert best is not None
    return best


def decimal_fraction(value: Decimal) -> Fraction:
    return Fraction(str(value))


def fraction_decimal(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def solve_linear_system(matrix: list[list[Fraction]], rhs: list[Fraction]) -> list[Fraction] | None:
    size = len(rhs)
    augmented = [list(row) + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = next((row for row in range(column, size) if augmented[row][column] != 0), None)
        if pivot is None:
            return None
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        augmented[column] = [value / pivot_value for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                augmented[row][item] - factor * augmented[column][item]
                for item in range(size + 1)
            ]
    return [augmented[row][-1] for row in range(size)]


def exhaustive_fractional_oracle(
    units: list[CommercialUnit],
    payments: list[PaymentSpec],
) -> tuple[Decimal, int]:
    medios = sorted(payments, key=lambda item: (item.medio.id, item.medio.codigo))
    fixed = [payment for payment in medios if payment.request.tipo == TipoSolicitudPago.IMPORTE_FIJO]
    rest = next((payment for payment in medios if payment.request.tipo == TipoSolicitudPago.RESTO), None)
    variables = [(unit_index, payment_index) for unit_index in range(len(units)) for payment_index in range(len(medios))]
    rows: list[list[Fraction]] = []
    rhs: list[Fraction] = []
    for unit_index in range(len(units)):
        rows.append([Fraction(1) if variable[0] == unit_index else Fraction(0) for variable in variables])
        rhs.append(Fraction(1))
    for payment in fixed:
        payment_index = medios.index(payment)
        condition_id = payment.medio.condicion_comercial_id
        rows.append(
            [
                decimal_fraction(units[unit_index].prices[condition_id]) if variable_payment_index == payment_index else Fraction(0)
                for unit_index, variable_payment_index in variables
            ]
        )
        rhs.append(decimal_fraction(Decimal(payment.request.importe or Decimal("0"))))

    rank = len(rows)
    best: tuple[Decimal, int] | None = None
    for basis in combinations(range(len(variables)), rank):
        matrix = [[row[index] for index in basis] for row in rows]
        solution = solve_linear_system(matrix, rhs)
        if solution is None:
            continue
        values = {index: Fraction(0) for index in range(len(variables))}
        for index, value in zip(basis, solution, strict=True):
            values[index] = value
        if any(value < 0 or value > 1 for value in values.values()):
            continue
        valid = True
        for row, expected in zip(rows, rhs, strict=True):
            if sum(row[index] * values[index] for index in range(len(variables))) != expected:
                valid = False
                break
        if not valid:
            continue

        medium_internal = {payment.medio.id: Decimal("0.000000") for payment in medios}
        by_unit: dict[int, list[Fraction]] = {unit_index: [] for unit_index in range(len(units))}
        for variable_index, fraction in values.items():
            if fraction == 0:
                continue
            unit_index, payment_index = variables[variable_index]
            payment = medios[payment_index]
            condition_id = payment.medio.condicion_comercial_id
            decimal_value = fraction_decimal(fraction)
            medium_internal[payment.medio.id] += units[unit_index].prices[condition_id] * decimal_value
            by_unit[unit_index].append(fraction)

        total = sum((Decimal(payment.request.importe or Decimal("0")) for payment in fixed), Decimal("0.00"))
        if rest is not None:
            total += round_money_up(medium_internal[rest.medio.id])
        fractional_units = sum(1 for fractions in by_unit.values() if len(fractions) > 1 or fractions != [Fraction(1)])
        score = (total.quantize(Decimal("0.01")), fractional_units)
        if best is None or score < best:
            best = score
    assert best is not None
    return best


def test_motor_coincide_con_oraculo_exhaustivo_en_escenarios_pequenos() -> None:
    scenarios = [
        (
            [engine_unit(1, {1: "10", 2: "10", 3: "100"}), engine_unit(2, {1: "10", 2: "100", 3: "99"}), engine_unit(3, {1: "100", 2: "10", 3: "1"})],
            [engine_payment(engine_medio(1, "A", 1), "10"), engine_payment(engine_medio(2, "B", 2), "10"), engine_payment(engine_medio(3, "R", 3))],
        ),
        (
            [engine_unit(1, {1: "10", 2: "12"}), engine_unit(2, {1: "10", 2: "8"}), engine_unit(3, {1: "10", 2: "30"})],
            [engine_payment(engine_medio(1, "A", 1), "20"), engine_payment(engine_medio(2, "B", 2))],
        ),
        (
            [engine_unit(1, {1: "10", 2: "10"}), engine_unit(2, {1: "10", 2: "10"}), engine_unit(3, {1: "20", 2: "20"})],
            [engine_payment(engine_medio(1, "A", 1), "10"), engine_payment(engine_medio(2, "B", 1), "30")],
        ),
    ]
    engine = CommercialResolutionEngine()
    for units, payments in scenarios:
        result = engine.resolve(venta_id=1, units=units, payments=payments)
        assert result.total == brute_force_full_units(units, payments)


def test_motor_coincide_con_oraculo_fraccionario_independiente() -> None:
    scenarios = [
        (
            [engine_unit(1, {1: "30000", 2: "33000"}), engine_unit(2, {1: "30000", 2: "33000"})],
            [engine_payment(engine_medio(1, "EFECTIVO", 1), "40000"), engine_payment(engine_medio(2, "VISA", 2))],
        ),
        (
            [engine_unit(1, {1: "10", 2: "10.01"}), engine_unit(2, {1: "10", 2: "10.01"}), engine_unit(3, {1: "10", 2: "10.02"})],
            [engine_payment(engine_medio(1, "A", 1), "10.01"), engine_payment(engine_medio(2, "R", 2))],
        ),
        (
            [engine_unit(1, {1: "10", 2: "10", 3: "100"}), engine_unit(2, {1: "10", 2: "100", 3: "99"}), engine_unit(3, {1: "100", 2: "10", 3: "1"})],
            [engine_payment(engine_medio(1, "A", 1), "10"), engine_payment(engine_medio(2, "B", 2), "10"), engine_payment(engine_medio(3, "R", 3))],
        ),
        (
            [engine_unit(1, {1: "200.03", 2: "100.01"})],
            [engine_payment(engine_medio(1, "A", 1), "0.02"), engine_payment(engine_medio(2, "R", 2))],
        ),
    ]
    engine = CommercialResolutionEngine()
    for units, payments in scenarios:
        result = engine.resolve(venta_id=1, units=units, payments=payments)
        assert (result.total, result.traza["unidades_fraccionadas"]) == exhaustive_fractional_oracle(units, payments)


def test_regresion_bucket_no_aplana_subtotal_interno_antes_de_redondear() -> None:
    medio_a = engine_medio(1, "A", 1)
    resto = engine_medio(2, "R", 2)
    units = [engine_unit(1, {1: "200.03", 2: "100.01"})]
    fixed_fraction = Fraction(2, 100) / Fraction(20003, 100)
    expected_rest = Fraction(10001, 100) * (Fraction(1) - fixed_fraction)

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(medio_a, "0.02"), engine_payment(resto)],
    )
    rest_trace = next(item for item in result.traza["resultados_por_medio"] if item["medio_pago_codigo"] == "R")
    rest_internal = Decimal(rest_trace["subtotal_interno"])

    assert fraction_decimal(expected_rest) > Decimal("100.00")
    assert fraction_decimal(expected_rest) < Decimal("100.05")
    assert rest_internal > Decimal("100.00")
    assert rest_internal < Decimal("100.05")
    assert result.pagos[1].importe_final == Decimal("100.05")
    assert result.total == Decimal("100.07")


def test_bucket_fronteras_se_calculan_sin_float() -> None:
    barely_above_100 = Fraction(10001, 100) * (Fraction(1) - Fraction(2, 100) / Fraction(20003, 100))

    assert round_money_up(Decimal("100.00")) == Decimal("100.00")
    assert round_money_up(fraction_decimal(barely_above_100)) == Decimal("100.05")
    assert round_money_up(Decimal("100.049999999999999999")) == Decimal("100.05")
    assert round_money_up(Decimal("100.05")) == Decimal("100.05")
    assert round_money_up(Decimal("100.050000000000000001")) == Decimal("100.10")


def test_mismo_bucket_prioriza_menos_unidades_fraccionadas_sobre_resto_interno() -> None:
    medio_a = engine_medio(1, "A", 1)
    medio_b = engine_medio(2, "B", 2)
    resto = engine_medio(3, "R", 3)
    units = [
        engine_unit(1, {1: "16.49", 2: "18.63", 3: "14.99"}),
        engine_unit(2, {1: "41.91", 2: "44.07", 3: "32.59"}),
        engine_unit(3, {1: "25.55", 2: "30.97", 3: "30.08"}),
        engine_unit(4, {1: "22.59", 2: "34.62", 3: "13.81"}),
    ]
    min_resto = (
        Fraction(3259, 100) * Fraction(2225680, 2422381)
        + Fraction(1381, 100)
    )
    chosen_resto = (
        Fraction(3259, 100) * Fraction(514460, 559689)
        + Fraction(1381, 100)
    )

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[
            engine_payment(resto),
            engine_payment(medio_b, "21.96"),
            engine_payment(medio_a, "25.77"),
        ],
    )
    rest_trace = next(item for item in result.traza["resultados_por_medio"] if item["medio_pago_codigo"] == "R")
    rest_internal = Decimal(rest_trace["subtotal_interno"])

    assert abs(fraction_decimal(min_resto) - Decimal(result.traza["resto_minimo_interno"])) < Decimal("0.000000000000001")
    assert abs(rest_internal - fraction_decimal(chosen_resto)) < Decimal("0.000000000000001")
    assert rest_internal > Decimal(result.traza["resto_minimo_interno"])
    assert round_money_up(fraction_decimal(min_resto)) == Decimal(result.traza["resto_bucket_final"])
    assert round_money_up(rest_internal) == Decimal(result.traza["resto_bucket_final"])
    assert result.traza["resto_bucket_final"] == "43.80"
    assert result.traza["unidades_fraccionadas"] == 1
    assert exhaustive_fractional_oracle(units, [engine_payment(medio_a, "25.77"), engine_payment(medio_b, "21.96"), engine_payment(resto)]) == (
        Decimal("91.53"),
        1,
    )
    assert result.total == Decimal("91.53")


def test_redondeo_monetario_participa_en_empate_y_desempate_canonico() -> None:
    medio_a = engine_medio(1, "A", 1)
    resto = engine_medio(2, "R", 2)
    units = [
        engine_unit(1, {1: "10.00", 2: "10.01"}),
        engine_unit(2, {1: "10.00", 2: "10.04"}),
    ]

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(resto), engine_payment(medio_a, "10.00")],
    )

    assert result.total == Decimal("20.05")
    assert [(item.medio_pago_codigo, item.articulo_id) for item in result.asignaciones] == [("A", 1), ("R", 2)]


def test_centavos_no_generan_falsa_infactibilidad() -> None:
    efectivo = engine_medio(1, "EFECTIVO", 1)
    visa = engine_medio(2, "VISA", 2)
    units = [
        engine_unit(1, {1: "10000.03", 2: "11000.07"}),
        engine_unit(2, {1: "20000.09", 2: "23000.11"}),
    ]

    result = CommercialResolutionEngine().resolve(
        venta_id=1,
        units=units,
        payments=[engine_payment(efectivo, "10000.03"), engine_payment(visa)],
    )

    assert result.total == Decimal("32500.18")
    assert result.pagos[0].importe_final == Decimal("10000.03")


def test_resuelve_tamano_moderado_100_unidades_4_medios() -> None:
    media = [engine_medio(index + 1, f"M{index + 1}", index + 1) for index in range(4)]
    units = []
    targets = {media[0].id: Decimal("0"), media[1].id: Decimal("0"), media[2].id: Decimal("0")}
    for unit_index in range(100):
        prices = {
            condition_id: Decimal(1000 + ((unit_index * 37 + condition_id * 173) % 9000))
            for condition_id in range(1, 5)
        }
        units.append(engine_unit(unit_index + 1, {condition_id: str(price) for condition_id, price in prices.items()}))
        if unit_index % 4 < 3:
            targets[media[unit_index % 4].id] += prices[unit_index % 4 + 1]
    payments = [
        engine_payment(media[0], str(targets[media[0].id])),
        engine_payment(media[1], str(targets[media[1].id])),
        engine_payment(media[2], str(targets[media[2].id])),
        engine_payment(media[3]),
    ]

    result = CommercialResolutionEngine().resolve(venta_id=1, units=units, payments=payments)

    assert result.traza["solver_statuses"]["fase_c"] == "kOptimal"
    assert result.traza["modelo"]["variables"] >= 800


def test_confirmacion_persiste_snapshot_y_cambios_posteriores_no_lo_alteran(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-SNAP", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "1")])
    service = CommercialService(db_session)

    snapshot = service.confirmar_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )
    PricingService(db_session).set_precio_manual(
        variante.articulo_id,
        PrecioManualArticuloRequest(condicion_comercial_id=ctx["visa"].id, precio=Decimal("15000")),  # type: ignore[union-attr]
    )
    service.update_medio(ctx["medio_visa"].id, MedioPagoUpdate(condicion_comercial_id=ctx["base"].id))  # type: ignore[union-attr]
    persisted = service.get_resolucion(venta_id)

    assert snapshot.total == Decimal("11000.00")
    assert persisted.resultado["total"] == "11000.00"
    assert persisted.resultado["pagos"][0]["condicion_comercial_id"] == ctx["visa"].id  # type: ignore[union-attr]


def test_venta_cerrada_rechaza_modificacion_y_confirmacion_atomica(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-CLOSED", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "1")])
    service = CommercialService(db_session)

    original_add = db_session.add

    def fail_on_snapshot(entity):
        if isinstance(entity, ResolucionComercialVenta):
            raise RuntimeError("fallo snapshot")
        return original_add(entity)

    monkeypatch.setattr(db_session, "add", fail_on_snapshot)
    with pytest.raises(RuntimeError):
        service.confirmar_resolucion(
            venta_id,
            ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
        )
    monkeypatch.setattr(db_session, "add", original_add)
    venta = VentaService(db_session).get(venta_id)
    assert venta.estado == VentaEstado.ABIERTA
    assert db_session.scalars(select(ResolucionComercialVenta)).all() == []

    service.confirmar_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
    )
    with pytest.raises(BusinessRuleViolation):
        VentaService(db_session).add_item(
            venta_id,
            DetalleVentaCreate(variante_id=variante.id, cantidad=Decimal("1"), precio_unitario=Decimal("1")),
        )
    with pytest.raises(BusinessRuleViolation):
        service.confirmar_resolucion(
            venta_id,
            ResolucionComercialRequest(pagos=[payment(ctx["medio_visa"])]),  # type: ignore[arg-type]
        )


def test_inventario_sigue_por_evento_e_idempotente(db_session: Session) -> None:
    ctx = setup_catalog(db_session)
    variante = add_article_with_prices(db_session, ctx, "ART-INV", "10000", "11000")
    venta_id = create_sale(db_session, ctx, [(variante, "2")])
    CommercialService(db_session).confirmar_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[payment(ctx["efectivo"])]),  # type: ignore[arg-type]
    )

    assert len(db_session.scalars(select(EventoPendiente)).all()) == 1
    first = VentaService(db_session).procesar_eventos_pendientes()
    second = VentaService(db_session).procesar_eventos_pendientes()

    assert first.procesados == 1
    assert second.procesados == 0
    assert len(db_session.scalars(select(MovimientoStock)).all()) == 1
    assert db_session.scalar(select(StockActual)).cantidad == -2  # type: ignore[union-attr]


def test_commercial_api_flow(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Comercial API"}).json()
    destino = client.post("/api/inventario/destinos", json={"codigo": "LOCAL", "nombre": "Local", "tipo": "LOCAL"}).json()
    base = client.post("/api/precios/condiciones", json={"codigo": "P1", "nombre": "Contado", "tipo": "BASE"}).json()
    visa = client.post(
        "/api/precios/condiciones",
        json={
            "codigo": "P2",
            "nombre": "Visa",
            "tipo": "DERIVADA",
            "condicion_base_id": base["id"],
            "tipo_regla": "INCREMENTO_PORCENTUAL",
            "porcentaje": "10",
        },
    ).json()
    efectivo = client.post("/api/medios-pago", json={"codigo": "EFECTIVO", "nombre": "Efectivo", "condicion_comercial_id": base["id"]}).json()
    medio_visa = client.post("/api/medios-pago", json={"codigo": "VISA", "nombre": "Visa", "condicion_comercial_id": visa["id"]}).json()
    articulo = client.post("/api/articulos", json={"codigo": "ART-API-COM", "nombre": "API", "categoria_id": categoria["id"]}).json()
    variante = client.post("/api/variantes", json={"articulo_id": articulo["id"], "codigo_barra": "7790000999000"}).json()
    client.put(f"/api/precios/articulos/{articulo['id']}/base", json={"precio": "10000"})
    venta = client.post("/api/ventas", json={"destino_id": destino["id"]}).json()
    client.post(f"/api/ventas/{venta['id']}/items", json={"variante_id": variante["id"], "cantidad": "1", "precio_unitario": "1"})

    quote = client.get(f"/api/ventas/{venta['id']}/comercial/cotizacion/medios/{medio_visa['id']}")
    simulation = client.post(
        f"/api/ventas/{venta['id']}/comercial/resolucion/simular",
        json={"pagos": [{"medio_pago_id": efectivo["id"], "tipo": "IMPORTE_FIJO", "importe": "5000"}, {"medio_pago_id": medio_visa["id"], "tipo": "RESTO"}]},
    )
    confirmation = client.post(
        f"/api/ventas/{venta['id']}/comercial/resolucion/confirmar",
        json={"pagos": [{"medio_pago_id": medio_visa["id"], "tipo": "RESTO"}]},
    )

    assert quote.status_code == 200
    assert quote.json()["total"] == "11000.00"
    assert simulation.status_code == 200
    assert simulation.json()["traza"]["asignaciones"]
    assert confirmation.status_code == 200
    assert confirmation.json()["total"] == "11000.00"
