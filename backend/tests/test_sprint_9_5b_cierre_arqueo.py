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
    ArqueoCaja,
    ArqueoCajaEstado,
    ArqueoCajaTipo,
    Articulo,
    Caja,
    Categoria,
    DestinoInventario,
    DestinoInventarioTipo,
    MedioPago,
    SesionCaja,
    SesionCajaEstado,
    Variante,
)
from backend.app.models.pricing import CondicionPrecioTipo, TipoReglaPrecio
from backend.app.schemas.caja import CerrarSesionCajaRequest, MovimientoCajaCreate
from backend.app.schemas.commercial import MedioPagoCreate, ResolucionComercialRequest
from backend.app.schemas.pricing import CondicionComercialPrecioCreate, PrecioBaseArticuloRequest
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate
from backend.app.services.caja_service import CajaService
from backend.app.services.commercial_service import CommercialService
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, ValidationError
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


def setup_cierre(db: Session, efectivo_inicial: str = "50000.00") -> dict[str, object]:
    categoria = Categoria(nombre="Cierre 9.5B")
    destino = DestinoInventario(codigo="LOCAL_95B", nombre="Local 9.5B", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_95B", nombre="Caja 9.5B")
    sesion = SesionCaja(caja=caja, cajero_id=100, efectivo_inicial=Decimal(efectivo_inicial))
    articulo = Articulo(codigo="ART-95B", nombre="Articulo 9.5B", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000952")
    db.add_all([categoria, destino, caja, sesion, articulo, variante])
    db.commit()
    pricing = PricingService(db)
    base = pricing.create_condicion(
        CondicionComercialPrecioCreate(codigo="CONTADO_95B", nombre="Contado", tipo=CondicionPrecioTipo.BASE)
    )
    visa_condition = pricing.create_condicion(
        CondicionComercialPrecioCreate(
            codigo="VISA_95B",
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
        MedioPagoCreate(codigo="EFECTIVO_95B", nombre="Efectivo", condicion_comercial_id=base.id, es_efectivo=True)
    )
    visa = commercial.create_medio(
        MedioPagoCreate(codigo="VISA_95B", nombre="Visa", condicion_comercial_id=visa_condition.id)
    )
    return {
        "destino": destino,
        "caja": caja,
        "sesion": sesion,
        "variante": variante,
        "efectivo": efectivo,
        "visa": visa,
    }


def create_captured_sale(db: Session, ctx: dict[str, object], quantity: str):
    service = VentaService(db)
    venta = service.create(VentaCreate(destino_id=ctx["destino"].id))  # type: ignore[union-attr]
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(
            variante_id=ctx["variante"].id,  # type: ignore[union-attr]
            cantidad=Decimal(quantity),
            precio_unitario=Decimal("1.00"),
        ),
    )
    venta = service.enviar_a_caja(venta.id, usuario_id=20)
    return service.capturar(venta.id, sesion_caja_id=ctx["sesion"].id, usuario_id=100)  # type: ignore[union-attr]


def confirm_sale(db: Session, venta_id: int, medio: MedioPago, usuario_id: int = 100):
    return CommercialService(db).confirmar_resolucion(
        venta_id,
        ResolucionComercialRequest(pagos=[{"medio_pago_id": medio.id, "tipo": "RESTO"}], usuario_id=usuario_id),
    )


def test_cierre_calcula_esperado_y_registra_arqueo_ciego(db_session: Session) -> None:
    ctx = setup_cierre(db_session)
    cash_sale = create_captured_sale(db_session, ctx, "20")
    electronic_sale = create_captured_sale(db_session, ctx, "30")
    confirm_sale(db_session, cash_sale.id, ctx["efectivo"])  # type: ignore[arg-type]
    confirm_sale(db_session, electronic_sale.id, ctx["visa"])  # type: ignore[arg-type]
    caja = CajaService(db_session)
    caja.registrar_movimiento(
        ctx["sesion"].id,  # type: ignore[union-attr]
        MovimientoCajaCreate(usuario_id=100, tipo="INGRESO", importe=Decimal("30000.00")),
    )
    caja.registrar_movimiento(
        ctx["sesion"].id,  # type: ignore[union-attr]
        MovimientoCajaCreate(usuario_id=100, tipo="RETIRO", importe=Decimal("150000.00")),
    )
    caja.registrar_movimiento(
        ctx["sesion"].id,  # type: ignore[union-attr]
        MovimientoCajaCreate(usuario_id=100, tipo="EGRESO", importe=Decimal("20000.00"), motivo="flete"),
    )

    response = caja.cerrar_sesion(
        ctx["sesion"].id,  # type: ignore[union-attr]
        CerrarSesionCajaRequest(
            usuario_id=100,
            efectivo_final_declarado=Decimal("108000.00"),
            observacion_cierre=" cierre turno ",
        ),
    )
    sesion = db_session.get(SesionCaja, ctx["sesion"].id)  # type: ignore[union-attr]
    arqueo = db_session.get(ArqueoCaja, response.arqueo_id)

    assert response.efectivo_esperado == Decimal("110000.00")
    assert response.primer_conteo == Decimal("108000.00")
    assert response.diferencia == Decimal("-2000.00")
    assert response.estado == SesionCajaEstado.CERRADA
    assert sesion is not None
    assert sesion.estado == SesionCajaEstado.CERRADA
    assert sesion.efectivo_final_declarado == Decimal("108000.00")
    assert sesion.observacion_cierre == "cierre turno"
    assert arqueo is not None
    assert arqueo.tipo == ArqueoCajaTipo.CIERRE
    assert arqueo.estado == ArqueoCajaEstado.REGISTRADO
    assert arqueo.efectivo_esperado == Decimal("110000.00")
    assert arqueo.primer_conteo == Decimal("108000.00")
    assert arqueo.diferencia == Decimal("-2000.00")
    assert arqueo.usuario_id == 100


def test_cierre_no_bloquea_diferencia_positiva(db_session: Session) -> None:
    ctx = setup_cierre(db_session, efectivo_inicial="100.00")

    response = CajaService(db_session).cerrar_sesion(
        ctx["sesion"].id,  # type: ignore[union-attr]
        CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("125.00")),
    )

    assert response.efectivo_esperado == Decimal("100.00")
    assert response.diferencia == Decimal("25.00")


def test_cierre_rechaza_otro_cajero_sesion_inexistente_y_declarado_negativo(db_session: Session) -> None:
    ctx = setup_cierre(db_session)
    service = CajaService(db_session)

    with pytest.raises(ValidationError):
        service.cerrar_sesion(
            ctx["sesion"].id,  # type: ignore[union-attr]
            CerrarSesionCajaRequest(usuario_id=101, efectivo_final_declarado=Decimal("0.00")),
        )
    with pytest.raises(Exception):
        CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("-0.01"))
    with pytest.raises(Exception):
        service.cerrar_sesion(999999, CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("0.00")))


def test_doble_cierre_no_modifica_arqueo_original(db_session: Session) -> None:
    ctx = setup_cierre(db_session)
    service = CajaService(db_session)
    first = service.cerrar_sesion(
        ctx["sesion"].id,  # type: ignore[union-attr]
        CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("10.00")),
    )

    with pytest.raises(ConflictError):
        service.cerrar_sesion(
            ctx["sesion"].id,  # type: ignore[union-attr]
            CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("99.00")),
        )
    arqueos = db_session.scalars(select(ArqueoCaja)).all()
    sesion = db_session.get(SesionCaja, ctx["sesion"].id)  # type: ignore[union-attr]

    assert len(arqueos) == 1
    assert arqueos[0].id == first.arqueo_id
    assert arqueos[0].primer_conteo == Decimal("10.00")
    assert sesion is not None
    assert sesion.efectivo_final_declarado == Decimal("10.00")


def test_cierre_rollback_no_persiste_arqueo_ni_cierra_sesion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = setup_cierre(db_session)
    original_flush = db_session.flush

    def fail_after_arqueo_prepared(*args, **kwargs):
        if any(isinstance(entity, ArqueoCaja) for entity in db_session.new):
            raise RuntimeError("fallo despues de preparar arqueo")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", fail_after_arqueo_prepared)

    with pytest.raises(RuntimeError):
        CajaService(db_session).cerrar_sesion(
            ctx["sesion"].id,  # type: ignore[union-attr]
            CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("10.00")),
        )

    db_session.expire_all()
    sesion = db_session.get(SesionCaja, ctx["sesion"].id)  # type: ignore[union-attr]

    assert sesion is not None
    assert sesion.estado == SesionCajaEstado.ABIERTA
    assert sesion.cerrada_at is None
    assert sesion.efectivo_final_declarado is None
    assert db_session.scalars(select(ArqueoCaja)).all() == []


def test_sesion_cerrada_bloquea_movimientos_y_cobros_posteriores(db_session: Session) -> None:
    ctx = setup_cierre(db_session)
    venta = create_captured_sale(db_session, ctx, "1")
    VentaService(db_session).add_pago(
        venta.id,
        PagoVentaCreate(medio_pago=ctx["efectivo"].codigo, importe=Decimal("10000.00")),  # type: ignore[union-attr]
    )
    CajaService(db_session).cerrar_sesion(
        ctx["sesion"].id,  # type: ignore[union-attr]
        CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("0.00")),
    )

    with pytest.raises(ValidationError):
        CajaService(db_session).registrar_movimiento(
            ctx["sesion"].id,  # type: ignore[union-attr]
            MovimientoCajaCreate(usuario_id=100, tipo="INGRESO", importe=Decimal("1.00")),
        )
    with pytest.raises(ValidationError):
        VentaService(db_session).finalizar(venta.id, usuario_id=100)
    with pytest.raises(BusinessRuleViolation):
        CommercialService(db_session).confirmar_resolucion(
            venta.id,
            ResolucionComercialRequest(pagos=[{"medio_pago_id": ctx["efectivo"].id, "tipo": "RESTO"}], usuario_id=100),  # type: ignore[union-attr]
        )


def test_caja_puede_abrir_nueva_sesion_tras_cierre(db_session: Session) -> None:
    ctx = setup_cierre(db_session)
    service = CajaService(db_session)
    service.cerrar_sesion(
        ctx["sesion"].id,  # type: ignore[union-attr]
        CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("0.00")),
    )

    opened = service.abrir_sesion(ctx["caja"].id, cajero_id=101, efectivo_inicial=Decimal("500.00"))  # type: ignore[union-attr]

    assert opened.sesion.caja_id == ctx["caja"].id  # type: ignore[union-attr]
    assert opened.sesion.cajero_id == 101
    assert opened.sesion.efectivo_inicial == Decimal("500.00")


def test_api_cerrar_sesion_caja(client: TestClient) -> None:
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_API_95B", "nombre": "Local API 9.5B", "tipo": "LOCAL"},
    ).json()
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    try:
        caja = Caja(destino_id=destino["id"], codigo="CAJA_API_95B", nombre="Caja API 9.5B")
        db.add(caja)
        db.commit()
    finally:
        db.close()
        db_generator.close()
    sesion = client.post(
        f"/api/cajas/{caja.id}/sesiones",
        json={"usuario_id": 100, "efectivo_inicial": "50.00"},
    ).json()["sesion"]

    closed = client.post(
        f"/api/cajas/sesiones/{sesion['id']}/cerrar",
        json={"usuario_id": 100, "efectivo_final_declarado": "40.00", "observacion_cierre": None},
    )
    double_close = client.post(
        f"/api/cajas/sesiones/{sesion['id']}/cerrar",
        json={"usuario_id": 100, "efectivo_final_declarado": "40.00"},
    )

    assert closed.status_code == 200
    assert closed.json()["estado"] == "CERRADA"
    assert closed.json()["efectivo_esperado"] == "50.00"
    assert closed.json()["primer_conteo"] == "40.00"
    assert closed.json()["diferencia"] == "-10.00"
    assert closed.json()["arqueo_id"] is not None
    assert double_close.status_code == 409
