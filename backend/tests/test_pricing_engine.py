from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.business.pricing import PRICE_BASE_REQUIRED, PRICE_CIRCULAR_DEPENDENCY, PRICE_RULE_INVALID
from backend.app.business.pricing.price_rules import PriceRuleEngine
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Articulo, Categoria, DetalleVenta, Variante
from backend.app.models.pricing import (
    AuditoriaPrecioArticulo,
    CondicionComercialPrecio,
    CondicionPrecioTipo,
    MotivoAuditoriaPrecio,
    OrigenPrecioArticulo,
    PrecioArticulo,
    TipoRedondeoPrecio,
    TipoReglaPrecio,
)
from backend.app.schemas.pricing import (
    AplicarRecalculoReglaRequest,
    CondicionComercialPrecioCreate,
    CondicionComercialPrecioUpdate,
    PrecioBaseArticuloRequest,
    PrecioManualArticuloRequest,
    RecalculoReglaPreviewRequest,
)
from backend.app.services.exceptions import BusinessRuleViolation
from backend.app.services.pricing_service import PricingService


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


def seed_article(db: Session, code: str = "ART-PRICE") -> Articulo:
    categoria = Categoria(nombre=f"Categoria {code}")
    articulo = Articulo(codigo=code, nombre=f"Articulo {code}", categoria=categoria)
    db.add_all([categoria, articulo])
    db.commit()
    return articulo


def create_base_and_derived(service: PricingService) -> tuple[CondicionComercialPrecio, CondicionComercialPrecio]:
    base = service.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="PRECIO_1",
            nombre="Contado",
            tipo=CondicionPrecioTipo.BASE,
            orden=1,
        )
    )
    derived = service.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="PRECIO_2",
            nombre="Precio 2",
            tipo=CondicionPrecioTipo.DERIVADA,
            condicion_base_id=base.id,
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("10"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            orden=2,
        )
    )
    return base, derived


def count_price_audits(db: Session) -> int:
    return db.scalar(select(func.count(AuditoriaPrecioArticulo.id))) or 0


def test_crear_condicion_base_e_impedir_base_invalida(db_session: Session) -> None:
    service = PricingService(db_session)

    base = service.create_condicion(
        CondicionComercialPrecioCreate(codigo="P1", nombre="Contado", tipo=CondicionPrecioTipo.BASE)
    )

    assert base.id is not None
    with pytest.raises(BusinessRuleViolation) as error:
        service.create_condicion(
            CondicionComercialPrecioCreate(
                codigo="P1_BAD",
                nombre="Base invalida",
                tipo=CondicionPrecioTipo.BASE,
                tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
                porcentaje=Decimal("10"),
            )
        )
    assert error.value.code == PRICE_RULE_INVALID


def test_crear_condicion_derivada_y_validar_dependencias(db_session: Session) -> None:
    service = PricingService(db_session)
    base, derived = create_base_and_derived(service)

    assert derived.condicion_base_id == base.id
    with pytest.raises(BusinessRuleViolation) as error:
        service.create_condicion(
            CondicionComercialPrecioCreate(codigo="P3", nombre="Sin base", tipo=CondicionPrecioTipo.DERIVADA)
        )
    assert error.value.code == PRICE_BASE_REQUIRED

    with pytest.raises(BusinessRuleViolation) as circular:
        service.update_condicion(derived.id, CondicionComercialPrecioUpdate(condicion_base_id=derived.id))
    assert circular.value.code == PRICE_CIRCULAR_DEPENDENCY


def test_calculo_incremento_descuento_redondeo_y_decimal() -> None:
    engine = PriceRuleEngine()

    inc = engine.calculate(
        precio_base=Decimal("10000"),
        tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
        porcentaje=Decimal("10"),
        tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
    )
    desc = engine.calculate(
        precio_base=Decimal("10000"),
        tipo_regla=TipoReglaPrecio.DESCUENTO_PORCENTUAL,
        porcentaje=Decimal("12.5"),
        tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
    )
    rounded = engine.calculate(
        precio_base=Decimal("9990"),
        tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
        porcentaje=Decimal("10"),
        tipo_redondeo=TipoRedondeoPrecio.MULTIPLO,
        multiplo_redondeo=Decimal("100"),
    )

    assert inc == Decimal("11000.00")
    assert desc == Decimal("8750.00")
    assert rounded == Decimal("11000.00")


def test_precio_base_genera_derivados_y_cambio_base_recalcula_manuales(db_session: Session) -> None:
    articulo = seed_article(db_session)
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)

    prices = service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    by_condition = {price.condicion_comercial_id: price for price in prices}

    assert by_condition[derived.id].precio == Decimal("11000.00")
    assert by_condition[derived.id].origen == OrigenPrecioArticulo.REGLA

    manual = service.set_precio_manual(
        articulo.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("10990")),
    )
    assert manual.origen == OrigenPrecioArticulo.MANUAL

    prices = service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("15000")))
    by_condition = {price.condicion_comercial_id: price for price in prices}
    assert by_condition[derived.id].precio == Decimal("16500.00")
    assert by_condition[derived.id].origen == OrigenPrecioArticulo.REGLA


def test_cambio_precio_base_es_atomico_ante_error(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    articulo = seed_article(db_session)
    service = PricingService(db_session)
    create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))

    def fail(*args, **kwargs):
        raise RuntimeError("fallo calculo")

    monkeypatch.setattr(service, "_calculate_all_derived", fail)
    with pytest.raises(RuntimeError):
        service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("15000")))

    prices = service.list_precios_articulo(articulo.id)
    assert {price.precio for price in prices} == {Decimal("10000.00"), Decimal("11000.00")}


def test_preview_no_modifica_db_y_cuenta_origenes(db_session: Session) -> None:
    articulo_a = seed_article(db_session, "ART-A")
    articulo_b = seed_article(db_session, "ART-B")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo_a.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_base(articulo_b.id, PrecioBaseArticuloRequest(precio=Decimal("20000")))
    service.set_precio_manual(
        articulo_b.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("21900")),
    )

    preview = service.preview_recalculo_regla(
        derived.id,
        RecalculoReglaPreviewRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("15"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
        ),
    )

    assert preview.articulos_afectados == 2
    assert preview.precios_regla == 1
    assert preview.precios_manual == 1
    assert {item.precio_propuesto for item in preview.items} == {Decimal("11500.00"), Decimal("23000.00")}
    assert service.get_precio_vigente(articulo_b.id, derived.id).precio == Decimal("21900.00")


def test_aplicar_recalculo_conservar_manuales(db_session: Session) -> None:
    articulo_a = seed_article(db_session, "ART-CONS-A")
    articulo_b = seed_article(db_session, "ART-CONS-B")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo_a.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_base(articulo_b.id, PrecioBaseArticuloRequest(precio=Decimal("20000")))
    service.set_precio_manual(
        articulo_b.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("21900")),
    )

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("15"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="CONSERVAR_MANUALES",
            confirmar=True,
        ),
    )

    assert result.precios_actualizados == 1
    assert result.precios_manual_conservados == 1
    assert service.get_precio_vigente(articulo_a.id, derived.id).precio == Decimal("11500.00")
    manual = service.get_precio_vigente(articulo_b.id, derived.id)
    assert manual.precio == Decimal("21900.00")
    assert manual.origen == OrigenPrecioArticulo.MANUAL


def test_aplicar_recalculo_a_todos_convierte_manual_a_regla_y_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-ALL")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_manual(
        articulo.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("10990")),
    )

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("15"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="APLICAR_REGLA_A_TODOS",
            confirmar=True,
        ),
    )
    price = service.get_precio_vigente(articulo.id, derived.id)
    audit = db_session.scalars(select(AuditoriaPrecioArticulo)).all()

    assert result.precios_actualizados == 1
    assert price.precio == Decimal("11500.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert audit[-1].motivo == MotivoAuditoriaPrecio.CAMBIO_REGLA_MASIVO


def test_recalculo_masivo_regla_mismo_precio_no_actualiza_ni_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-NOOP")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    price = service.get_precio_vigente(articulo.id, derived.id)
    updated_at = price.updated_at
    audit_count = count_price_audits(db_session)

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("10"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="APLICAR_REGLA_A_TODOS",
            confirmar=True,
        ),
    )
    db_session.refresh(price)

    assert result.articulos_afectados == 1
    assert result.precios_actualizados == 0
    assert price.precio == Decimal("11000.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert price.updated_at == updated_at
    assert count_price_audits(db_session) == audit_count


def test_recalculo_masivo_manual_mismo_precio_a_regla_actualiza_y_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-MANUAL-SAME")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_manual(
        articulo.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("11000")),
    )
    audit_count = count_price_audits(db_session)

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("10"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="APLICAR_REGLA_A_TODOS",
            confirmar=True,
        ),
    )
    price = service.get_precio_vigente(articulo.id, derived.id)

    assert result.precios_actualizados == 1
    assert price.precio == Decimal("11000.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert count_price_audits(db_session) == audit_count + 1


def test_recalculo_masivo_regla_precio_diferente_actualiza_y_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-RULE-DIFF")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("12000")))
    audit_count = count_price_audits(db_session)

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("15"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="APLICAR_REGLA_A_TODOS",
            confirmar=True,
        ),
    )
    price = service.get_precio_vigente(articulo.id, derived.id)

    assert result.precios_actualizados == 1
    assert price.precio == Decimal("13800.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert count_price_audits(db_session) == audit_count + 1


def test_recalculo_masivo_manual_precio_diferente_a_regla_actualiza_y_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-MANUAL-DIFF")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_manual(
        articulo.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("10700")),
    )
    audit_count = count_price_audits(db_session)

    result = service.aplicar_recalculo_regla(
        derived.id,
        AplicarRecalculoReglaRequest(
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("15"),
            tipo_redondeo=TipoRedondeoPrecio.SIN_REDONDEO,
            politica_manuales="APLICAR_REGLA_A_TODOS",
            confirmar=True,
        ),
    )
    price = service.get_precio_vigente(articulo.id, derived.id)

    assert result.precios_actualizados == 1
    assert price.precio == Decimal("11500.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert count_price_audits(db_session) == audit_count + 1


def test_precio_base_mismo_valor_no_audita_derivado_regla_sin_cambios(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-BASE-NOOP")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    price = service.get_precio_vigente(articulo.id, derived.id)
    updated_at = price.updated_at
    audit_count = count_price_audits(db_session)

    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    db_session.refresh(price)

    assert price.precio == Decimal("11000.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert price.updated_at == updated_at
    assert count_price_audits(db_session) == audit_count


def test_precio_base_mismo_resultado_elimina_override_manual_y_audita(db_session: Session) -> None:
    articulo = seed_article(db_session, "ART-BASE-MANUAL")
    service = PricingService(db_session)
    _, derived = create_base_and_derived(service)
    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    service.set_precio_manual(
        articulo.id,
        PrecioManualArticuloRequest(condicion_comercial_id=derived.id, precio=Decimal("11000")),
    )
    audit_count = count_price_audits(db_session)

    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    price = service.get_precio_vigente(articulo.id, derived.id)

    assert price.precio == Decimal("11000.00")
    assert price.origen == OrigenPrecioArticulo.REGLA
    assert count_price_audits(db_session) == audit_count + 1


def test_precio_vigente_y_venta_historica_no_cambia(db_session: Session) -> None:
    categoria = Categoria(nombre="Ventas")
    articulo = Articulo(codigo="ART-SNAP-PRICE", nombre="Articulo venta", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000999")
    db_session.add_all([categoria, articulo, variante])
    db_session.commit()
    service = PricingService(db_session)
    create_base_and_derived(service)
    prices = service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000")))
    current = prices[0]
    detalle = DetalleVenta(
        venta_id=1,
        variante_id=variante.id,
        codigo_articulo=articulo.codigo,
        codigo_barra=variante.codigo_barra,
        descripcion=articulo.nombre,
        cantidad=Decimal("1"),
        precio_unitario=current.precio,
        importe=current.precio,
    )
    db_session.add(detalle)
    db_session.commit()

    service.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("15000")))

    assert service.get_precio_vigente(articulo.id, current.condicion_comercial_id).precio == Decimal("15000.00")
    assert db_session.get(DetalleVenta, detalle.id).precio_unitario == Decimal("10000.00")


def test_pricing_api_flow(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Indumentaria"}).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "ART-API-PRICE", "nombre": "Remera", "categoria_id": categoria["id"]},
    ).json()
    base = client.post(
        "/api/precios/condiciones",
        json={"codigo": "PRECIO_1", "nombre": "Contado", "tipo": "BASE"},
    )
    derived = client.post(
        "/api/precios/condiciones",
        json={
            "codigo": "PRECIO_2",
            "nombre": "Precio 2",
            "tipo": "DERIVADA",
            "condicion_base_id": base.json()["id"],
            "tipo_regla": "INCREMENTO_PORCENTUAL",
            "porcentaje": "10",
        },
    )
    prices = client.put(f"/api/precios/articulos/{articulo['id']}/base", json={"precio": "10000"})
    current = client.get(f"/api/precios/articulos/{articulo['id']}/condiciones/{derived.json()['id']}")
    manual = client.put(
        f"/api/precios/articulos/{articulo['id']}/manual",
        json={"condicion_comercial_id": derived.json()["id"], "precio": "10990"},
    )
    preview = client.post(
        f"/api/precios/condiciones/{derived.json()['id']}/recalculo/preview",
        json={"tipo_regla": "INCREMENTO_PORCENTUAL", "porcentaje": "15", "tipo_redondeo": "SIN_REDONDEO"},
    )

    assert base.status_code == 201
    assert derived.status_code == 201
    assert prices.status_code == 200
    assert current.json()["precio"] == "11000.00"
    assert manual.json()["origen"] == "MANUAL"
    assert preview.json()["articulos_afectados"] == 1
