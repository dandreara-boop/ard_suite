from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
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
    EventoOperacionVenta,
    EventoOperacionVentaTipo,
    SesionCaja,
    SesionCajaEstado,
    Variante,
    VentaEstado,
)
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate
from backend.app.services.caja_service import CajaService
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, NotFoundError, ValidationError
from backend.app.services.venta_service import SALE_ALREADY_CAPTURED, VentaService


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


def seed_base(db: Session) -> dict[str, int]:
    categoria = Categoria(nombre="Sprint 9.3")
    articulo = Articulo(codigo="ART-93", nombre="Articulo 9.3", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000930")
    destino = DestinoInventario(codigo="LOCAL_93", nombre="Local 9.3", tipo=DestinoInventarioTipo.LOCAL)
    otro_destino = DestinoInventario(codigo="OTRO_93", nombre="Otro 9.3", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_93_1", nombre="Caja 9.3 1")
    caja_2 = Caja(destino=destino, codigo="CAJA_93_2", nombre="Caja 9.3 2")
    caja_3 = Caja(destino=destino, codigo="CAJA_93_3", nombre="Caja 9.3 3")
    caja_otro_destino = Caja(destino=otro_destino, codigo="CAJA_93_OTRA", nombre="Caja otra")
    caja_inactiva = Caja(destino=destino, codigo="CAJA_93_INACTIVA", nombre="Caja inactiva", activa=False)
    db.add_all(
        [
            categoria,
            articulo,
            variante,
            destino,
            otro_destino,
            caja,
            caja_2,
            caja_3,
            caja_otro_destino,
            caja_inactiva,
        ]
    )
    db.commit()
    return {
        "destino_id": destino.id,
        "variante_id": variante.id,
        "caja_id": caja.id,
        "caja_2_id": caja_2.id,
        "caja_3_id": caja_3.id,
        "caja_otro_destino_id": caja_otro_destino.id,
        "caja_inactiva_id": caja_inactiva.id,
    }


def ready_sale(service: VentaService, ids: dict[str, int]):
    venta = service.create(VentaCreate(destino_id=ids["destino_id"], usuario_id=10, vendedor_id=20))
    return service.enviar_a_caja(venta.id, usuario_id=20)


def open_session(db: Session, caja_id: int, cajero_id: int = 100) -> SesionCaja:
    response = CajaService(db).abrir_sesion(caja_id, cajero_id=cajero_id, efectivo_inicial=Decimal("0.00"))
    sesion = db.get(SesionCaja, response.sesion.id)
    assert sesion is not None
    return sesion


def test_apertura_sesion_valida_cero_negativo_caja_inexistente_e_inactiva(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = CajaService(db_session)

    abierta = service.abrir_sesion(ids["caja_id"], cajero_id=100, efectivo_inicial=Decimal("150.50"))
    cero = service.abrir_sesion(ids["caja_2_id"], cajero_id=101, efectivo_inicial=Decimal("0.00"))

    assert abierta.sesion.estado == SesionCajaEstado.ABIERTA
    assert abierta.sesion.efectivo_inicial == Decimal("150.50")
    assert cero.sesion.efectivo_inicial == Decimal("0.00")
    with pytest.raises(ValidationError):
        service.abrir_sesion(ids["caja_3_id"], cajero_id=102, efectivo_inicial=Decimal("-0.01"))
    with pytest.raises(NotFoundError):
        service.abrir_sesion(999999, cajero_id=102, efectivo_inicial=Decimal("0.00"))
    with pytest.raises(ValidationError):
        service.abrir_sesion(ids["caja_inactiva_id"], cajero_id=102, efectivo_inicial=Decimal("0.00"))


def test_una_sola_sesion_abierta_por_caja_y_mismo_cajero_en_varias_cajas(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = CajaService(db_session)

    first = service.abrir_sesion(ids["caja_id"], cajero_id=100, efectivo_inicial=Decimal("1.00"))
    second = service.abrir_sesion(ids["caja_2_id"], cajero_id=100, efectivo_inicial=Decimal("2.00"))
    abiertas = service.listar_abiertas_por_cajero(100)

    assert first.advertencia_multiples_sesiones is False
    assert second.advertencia_multiples_sesiones is True
    assert second.otras_sesiones_abiertas[0].sesion_id == first.sesion.id
    assert {sesion.caja_id for sesion in abiertas} == {ids["caja_id"], ids["caja_2_id"]}
    with pytest.raises(ConflictError):
        service.abrir_sesion(ids["caja_id"], cajero_id=101, efectivo_inicial=Decimal("3.00"))


def test_apertura_concurrente_sobre_misma_caja_deja_una_sola_sesion(tmp_path) -> None:
    db_path = tmp_path / "cash_sessions.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as db:
        ids = seed_base(db)

    def attempt_open(cajero_id: int) -> str:
        with TestingSessionLocal() as db:
            try:
                CajaService(db).abrir_sesion(
                    ids["caja_id"],
                    cajero_id=cajero_id,
                    efectivo_inicial=Decimal("0.00"),
                )
                return "opened"
            except ConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt_open, [100, 101]))

    with TestingSessionLocal() as db:
        abiertas = db.scalars(
            select(SesionCaja).where(SesionCaja.caja_id == ids["caja_id"], SesionCaja.estado == SesionCajaEstado.ABIERTA)
        ).all()
    Base.metadata.drop_all(bind=engine)

    assert sorted(results) == ["conflict", "opened"]
    assert len(abiertas) == 1


def test_api_lista_abiertas_y_no_elije_sesion_automaticamente(client: TestClient) -> None:
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_API_93", "nombre": "Local API 9.3", "tipo": "LOCAL"},
    ).json()
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    try:
        caja_1 = Caja(destino_id=destino["id"], codigo="CAJA_API_93_1", nombre="Caja API 9.3 1")
        caja_2 = Caja(destino_id=destino["id"], codigo="CAJA_API_93_2", nombre="Caja API 9.3 2")
        db.add_all([caja_1, caja_2])
        db.commit()
    finally:
        db.close()
        db_generator.close()

    first = client.post(f"/api/cajas/{caja_1.id}/sesiones", json={"usuario_id": 100, "efectivo_inicial": "0.00"})
    second = client.post(f"/api/cajas/{caja_2.id}/sesiones", json={"usuario_id": 100, "efectivo_inicial": "5.00"})
    abiertas = client.get("/api/cajas/sesiones/abiertas", params={"usuario_id": 100})
    venta = client.post("/api/ventas", json={"destino_id": destino["id"]}).json()
    client.post(f"/api/ventas/{venta['id']}/enviar-a-caja", json={"usuario_id": 20})
    capture_without_session = client.post(f"/api/ventas/{venta['id']}/capturar", json={"usuario_id": 100})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["advertencia_multiples_sesiones"] is True
    assert len(abiertas.json()) == 2
    assert capture_without_session.status_code == 422


def test_captura_valida_sesion_responsable_destino_y_concurrencia(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = VentaService(db_session)
    venta = ready_sale(service, ids)
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    sesion_2 = open_session(db_session, ids["caja_2_id"], cajero_id=101)
    sesion_otro_destino = open_session(db_session, ids["caja_otro_destino_id"], cajero_id=100)

    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)

    assert captured.estado == VentaEstado.EN_COBRO
    assert captured.caja_captura_id == ids["caja_id"]
    assert captured.sesion_caja_id == sesion.id
    with pytest.raises(NotFoundError):
        service.capturar(venta.id, sesion_caja_id=999999, usuario_id=100)
    with pytest.raises(ValidationError):
        service.capturar(venta.id, sesion_caja_id=sesion_2.id, usuario_id=100)
    with pytest.raises(BusinessRuleViolation) as error:
        service.capturar(venta.id, sesion_caja_id=sesion_2.id, usuario_id=101)
    assert error.value.code == SALE_ALREADY_CAPTURED

    otra_venta = ready_sale(service, ids)
    with pytest.raises(ValidationError):
        service.capturar(otra_venta.id, sesion_caja_id=sesion_otro_destino.id, usuario_id=100)
    sesion.estado = SesionCajaEstado.CERRADA
    db_session.commit()
    with pytest.raises(ValidationError):
        service.capturar(otra_venta.id, sesion_caja_id=sesion.id, usuario_id=100)


def test_liberacion_limpia_venta_y_conserva_auditoria(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = VentaService(db_session)
    venta = ready_sale(service, ids)
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)

    released = service.liberar(captured.id, usuario_id=100)
    events = db_session.scalars(
        select(EventoOperacionVenta)
        .where(EventoOperacionVenta.venta_id == venta.id)
        .order_by(EventoOperacionVenta.id)
    ).all()
    release_event = events[-1]

    assert released.estado == VentaEstado.LISTA_PARA_COBRAR
    assert released.numero_corto == venta.numero_corto
    assert released.vendedor_id == venta.vendedor_id
    assert released.referencia_cliente == venta.referencia_cliente
    assert released.caja_captura_id is None
    assert released.sesion_caja_id is None
    assert released.capturada_at is None
    assert release_event.tipo == EventoOperacionVentaTipo.LIBERACION
    assert release_event.caja_id == ids["caja_id"]
    assert release_event.sesion_caja_id == sesion.id
    assert release_event.payload["sesion_caja_anterior_id"] == sesion.id


def test_confirmacion_capturada_exige_sesion_abierta_y_responsable(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = VentaService(db_session)
    venta = ready_sale(service, ids)
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)
    service.add_item(
        captured.id,
        DetalleVentaCreate(variante_id=ids["variante_id"], cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),
    )
    service.add_pago(captured.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=Decimal("10.00")))

    with pytest.raises(ValidationError):
        service.finalizar(captured.id)
    with pytest.raises(ValidationError):
        service.finalizar(captured.id, usuario_id=101)
    closed = service.finalizar(captured.id, usuario_id=100)
    confirmation = db_session.scalars(
        select(EventoOperacionVenta)
        .where(
            EventoOperacionVenta.venta_id == closed.id,
            EventoOperacionVenta.tipo == EventoOperacionVentaTipo.CONFIRMACION,
        )
        .order_by(EventoOperacionVenta.id)
    ).all()[-1]

    assert closed.estado == VentaEstado.CERRADA
    assert closed.sesion_caja_id == sesion.id
    assert closed.caja_captura_id == ids["caja_id"]
    assert confirmation.sesion_caja_id == sesion.id

    venta_2 = ready_sale(service, ids)
    sesion_2 = open_session(db_session, ids["caja_2_id"], cajero_id=100)
    captured_2 = service.capturar(venta_2.id, sesion_caja_id=sesion_2.id, usuario_id=100)
    service.add_item(
        captured_2.id,
        DetalleVentaCreate(variante_id=ids["variante_id"], cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),
    )
    service.add_pago(captured_2.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=Decimal("10.00")))
    sesion_2.estado = SesionCajaEstado.CERRADA
    db_session.commit()
    with pytest.raises(ValidationError):
        service.finalizar(captured_2.id, usuario_id=100)


def test_finalizacion_legacy_abierta_permite_omitir_usuario_id(db_session: Session) -> None:
    ids = seed_base(db_session)
    service = VentaService(db_session)
    venta = service.create(VentaCreate(destino_id=ids["destino_id"]))
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(variante_id=ids["variante_id"], cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),
    )
    service.add_pago(venta.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=Decimal("10.00")))

    closed = service.finalizar(venta.id)

    assert closed.estado == VentaEstado.CERRADA
    assert closed.sesion_caja_id is None
