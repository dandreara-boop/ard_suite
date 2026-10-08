from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    Articulo,
    Caja,
    Categoria,
    DestinoInventario,
    DestinoInventarioTipo,
    MedioPago,
    PagoVenta,
    SesionCaja,
    Variante,
    VentaEstado,
)
from backend.app.models.pricing import CondicionPrecioTipo, TipoReglaPrecio
from backend.app.schemas.commercial import MedioPagoCreate, MedioPagoUpdate, ResolucionComercialRequest
from backend.app.schemas.pricing import CondicionComercialPrecioCreate, PrecioBaseArticuloRequest
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate
from backend.app.services.caja_service import CajaService
from backend.app.services.commercial_service import CommercialService
from backend.app.services.exceptions import ConflictError
from backend.app.services.pricing_service import PricingService
from backend.app.services.venta_service import VentaService


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


def setup_cash_sales(db: Session) -> dict[str, object]:
    categoria = Categoria(nombre="Efectivo 9.5A")
    destino = DestinoInventario(codigo="LOCAL_95A", nombre="Local 9.5A", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_95A_1", nombre="Caja 9.5A 1")
    caja_2 = Caja(destino=destino, codigo="CAJA_95A_2", nombre="Caja 9.5A 2")
    sesion = SesionCaja(caja=caja, cajero_id=100, efectivo_inicial=Decimal("0.00"))
    otra_sesion = SesionCaja(caja=caja_2, cajero_id=101, efectivo_inicial=Decimal("0.00"))
    articulo = Articulo(codigo="ART-95A", nombre="Articulo 9.5A", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000951")
    db.add_all([categoria, destino, caja, caja_2, sesion, otra_sesion, articulo, variante])
    db.commit()

    pricing = PricingService(db)
    base = pricing.create_condicion(
        CondicionComercialPrecioCreate(codigo="CONTADO_95A", nombre="Contado", tipo=CondicionPrecioTipo.BASE)
    )
    visa_condition = pricing.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="VISA_95A",
            nombre="Visa",
            tipo=CondicionPrecioTipo.DERIVADA,
            condicion_base_id=base.id,
            tipo_regla=TipoReglaPrecio.INCREMENTO_PORCENTUAL,
            porcentaje=Decimal("0"),
        )
    )
    pricing.set_precio_base(articulo.id, PrecioBaseArticuloRequest(precio=Decimal("10000.00")))
    commercial = CommercialService(db)
    efectivo = commercial.create_medio(
        MedioPagoCreate(codigo="EFECTIVO_95A", nombre="Efectivo", condicion_comercial_id=base.id, es_efectivo=True)
    )
    visa = commercial.create_medio(
        MedioPagoCreate(codigo="VISA_95A", nombre="Visa", condicion_comercial_id=visa_condition.id)
    )
    return {
        "destino": destino,
        "sesion": sesion,
        "otra_sesion": otra_sesion,
        "variante": variante,
        "efectivo": efectivo,
        "visa": visa,
    }


def create_captured_sale(db: Session, ctx: dict[str, object], sesion: SesionCaja | None = None):
    service = VentaService(db)
    target_session = sesion or ctx["sesion"]
    venta = service.create(VentaCreate(destino_id=ctx["destino"].id))  # type: ignore[union-attr]
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(
            variante_id=ctx["variante"].id,  # type: ignore[union-attr]
            cantidad=Decimal("3"),
            precio_unitario=Decimal("1.00"),
        ),
    )
    venta = service.enviar_a_caja(venta.id, usuario_id=20)
    return service.capturar(
        venta.id,
        sesion_caja_id=target_session.id,
        usuario_id=target_session.cajero_id,
    )


def test_medio_pago_es_efectivo_crud_y_unicidad(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    service = CommercialService(db_session)

    normal = service.create_medio(
        MedioPagoCreate(codigo="TRANSFER_95A", nombre="Transferencia", condicion_comercial_id=ctx["efectivo"].condicion_comercial_id)  # type: ignore[union-attr]
    )
    assert normal.es_efectivo is False
    assert service.get_medio(ctx["efectivo"].id).es_efectivo is True  # type: ignore[union-attr]
    with pytest.raises(ConflictError):
        service.create_medio(
            MedioPagoCreate(codigo="CASH_2_95A", nombre="Caja 2", condicion_comercial_id=normal.condicion_comercial_id, es_efectivo=True)
        )

    service.update_medio(ctx["efectivo"].id, MedioPagoUpdate(es_efectivo=True))  # type: ignore[union-attr]
    service.update_medio(ctx["efectivo"].id, MedioPagoUpdate(es_efectivo=False))  # type: ignore[union-attr]
    marked = service.update_medio(normal.id, MedioPagoUpdate(es_efectivo=True))

    assert marked.es_efectivo is True
    assert service.get_medio(ctx["efectivo"].id).es_efectivo is False  # type: ignore[union-attr]


def test_medio_pago_api_expone_es_efectivo(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "API 95A"}).json()
    condicion = client.post("/api/precios/condiciones", json={"codigo": "API_95A", "nombre": "Contado", "tipo": "BASE"}).json()
    del categoria

    normal = client.post(
        "/api/medios-pago",
        json={"codigo": "NORMAL_API_95A", "nombre": "Normal", "condicion_comercial_id": condicion["id"]},
    )
    efectivo = client.post(
        "/api/medios-pago",
        json={
            "codigo": "EFECTIVO_API_95A",
            "nombre": "Efectivo",
            "condicion_comercial_id": condicion["id"],
            "es_efectivo": True,
        },
    )
    duplicate = client.post(
        "/api/medios-pago",
        json={
            "codigo": "EFECTIVO_API_95A_2",
            "nombre": "Efectivo 2",
            "condicion_comercial_id": condicion["id"],
            "es_efectivo": True,
        },
    )

    assert normal.status_code == 201
    assert normal.json()["es_efectivo"] is False
    assert efectivo.status_code == 201
    assert efectivo.json()["es_efectivo"] is True
    assert duplicate.status_code == 409


def test_pago_comercial_guarda_medio_pago_id_y_snapshot(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    venta = create_captured_sale(db_session, ctx)

    CommercialService(db_session).confirmar_resolucion(
        venta.id,
        ResolucionComercialRequest(
            pagos=[
                {"medio_pago_id": ctx["efectivo"].id, "tipo": "IMPORTE_FIJO", "importe": Decimal("10000.00")},  # type: ignore[union-attr]
                {"medio_pago_id": ctx["visa"].id, "tipo": "RESTO"},  # type: ignore[union-attr]
            ],
            usuario_id=100,
        ),
    )
    pagos = db_session.scalars(select(PagoVenta).where(PagoVenta.venta_id == venta.id).order_by(PagoVenta.id)).all()

    assert [(pago.medio_pago_id, pago.medio_pago, pago.importe) for pago in pagos] == [
        (ctx["efectivo"].id, ctx["efectivo"].codigo, Decimal("10000.00")),  # type: ignore[union-attr]
        (ctx["visa"].id, ctx["visa"].codigo, Decimal("20000.00")),  # type: ignore[union-attr]
    ]
    assert pagos[0].medio_pago_ref == ctx["efectivo"]
    assert pagos[1].medio_pago_ref == ctx["visa"]


def test_pagos_legacy_resuelven_por_codigo_o_quedan_sin_fk(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    service = VentaService(db_session)
    venta = service.create(VentaCreate(destino_id=ctx["destino"].id))  # type: ignore[union-attr]
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(variante_id=ctx["variante"].id, cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),  # type: ignore[union-attr]
    )

    venta = service.add_pago(venta.id, PagoVentaCreate(medio_pago=ctx["efectivo"].codigo, importe=Decimal("5.00")))  # type: ignore[union-attr]
    venta = service.add_pago(venta.id, PagoVentaCreate(medio_pago="NO_EXISTE", importe=Decimal("5.00")))

    assert venta.pagos[0].medio_pago_id == ctx["efectivo"].id  # type: ignore[union-attr]
    assert venta.pagos[0].medio_pago == ctx["efectivo"].codigo  # type: ignore[union-attr]
    assert venta.pagos[1].medio_pago_id is None
    assert db_session.scalar(select(MedioPago).where(MedioPago.codigo == "NO_EXISTE")) is None


def test_no_permite_cambiar_es_efectivo_de_medio_con_pagos_historicos(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    venta = create_captured_sale(db_session, ctx)
    CommercialService(db_session).confirmar_resolucion(
        venta.id,
        ResolucionComercialRequest(pagos=[{"medio_pago_id": ctx["efectivo"].id, "tipo": "RESTO"}], usuario_id=100),  # type: ignore[union-attr]
    )

    with pytest.raises(ConflictError):
        CommercialService(db_session).update_medio(ctx["efectivo"].id, MedioPagoUpdate(es_efectivo=False))  # type: ignore[union-attr]


def test_total_efectivo_sesion_sin_ventas_es_cero(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)

    assert CajaService(db_session).total_pagos_efectivo_sesion(ctx["sesion"].id) == Decimal("0.00")  # type: ignore[union-attr]


def test_total_efectivo_sesion_con_pago_mixto_y_varias_ventas(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    commercial = CommercialService(db_session)
    caja = CajaService(db_session)
    venta_mixta = create_captured_sale(db_session, ctx)
    venta_efectivo = create_captured_sale(db_session, ctx)
    venta_electronica = create_captured_sale(db_session, ctx)
    venta_otra_sesion = create_captured_sale(db_session, ctx, sesion=ctx["otra_sesion"])  # type: ignore[arg-type]

    commercial.confirmar_resolucion(
        venta_mixta.id,
        ResolucionComercialRequest(
            pagos=[
                {"medio_pago_id": ctx["efectivo"].id, "tipo": "IMPORTE_FIJO", "importe": Decimal("10000.00")},  # type: ignore[union-attr]
                {"medio_pago_id": ctx["visa"].id, "tipo": "RESTO"},  # type: ignore[union-attr]
            ],
            usuario_id=100,
        ),
    )
    commercial.confirmar_resolucion(
        venta_efectivo.id,
        ResolucionComercialRequest(pagos=[{"medio_pago_id": ctx["efectivo"].id, "tipo": "RESTO"}], usuario_id=100),  # type: ignore[union-attr]
    )
    commercial.confirmar_resolucion(
        venta_electronica.id,
        ResolucionComercialRequest(pagos=[{"medio_pago_id": ctx["visa"].id, "tipo": "RESTO"}], usuario_id=100),  # type: ignore[union-attr]
    )
    commercial.confirmar_resolucion(
        venta_otra_sesion.id,
        ResolucionComercialRequest(pagos=[{"medio_pago_id": ctx["efectivo"].id, "tipo": "RESTO"}], usuario_id=101),  # type: ignore[union-attr]
    )

    assert caja.total_pagos_efectivo_sesion(ctx["sesion"].id) == Decimal("40000.00")  # type: ignore[union-attr]
    assert caja.total_pagos_efectivo_sesion(ctx["otra_sesion"].id) == Decimal("30000.00")  # type: ignore[union-attr]


def test_total_efectivo_ignora_ventas_no_cerradas_y_medios_no_efectivos(db_session: Session) -> None:
    ctx = setup_cash_sales(db_session)
    venta = create_captured_sale(db_session, ctx)
    VentaService(db_session).add_pago(
        venta.id,
        PagoVentaCreate(medio_pago=ctx["efectivo"].codigo, importe=Decimal("10000.00")),  # type: ignore[union-attr]
    )

    assert venta.estado == VentaEstado.EN_COBRO
    assert CajaService(db_session).total_pagos_efectivo_sesion(ctx["sesion"].id) == Decimal("0.00")  # type: ignore[union-attr]
