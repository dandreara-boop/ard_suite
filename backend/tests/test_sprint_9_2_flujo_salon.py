from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
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
    SecuenciaNumeroCortoVenta,
    SesionCaja,
    Variante,
    Venta,
    VentaEstado,
    VentaTipoAtencion,
)
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate, VentaUpdate
from backend.app.services.exceptions import BusinessRuleViolation, ValidationError
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


def seed_salon(db: Session) -> dict[str, int]:
    categoria = Categoria(nombre="Salon")
    articulo = Articulo(codigo="ART-SALON", nombre="Articulo Salon", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000901")
    destino = DestinoInventario(codigo="LOCAL_SALON", nombre="Local Salon", tipo=DestinoInventarioTipo.LOCAL)
    otro_destino = DestinoInventario(codigo="LOCAL_OTRO", nombre="Local Otro", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_SALON_1", nombre="Caja Salon 1")
    caja_2 = Caja(destino=destino, codigo="CAJA_SALON_2", nombre="Caja Salon 2")
    caja_otro_destino = Caja(destino=otro_destino, codigo="CAJA_OTRO", nombre="Caja Otro")
    caja_inactiva = Caja(destino=destino, codigo="CAJA_INACTIVA", nombre="Caja Inactiva", activa=False)
    db.add_all([categoria, articulo, variante, destino, otro_destino, caja, caja_2, caja_otro_destino, caja_inactiva])
    db.commit()
    return {
        "destino_id": destino.id,
        "otro_destino_id": otro_destino.id,
        "variante_id": variante.id,
        "caja_id": caja.id,
        "caja_2_id": caja_2.id,
        "caja_otro_destino_id": caja_otro_destino.id,
        "caja_inactiva_id": caja_inactiva.id,
    }


def create_sale(service: VentaService, destino_id: int, *, referencia: str = "Ana") -> Venta:
    venta = service.create(
        VentaCreate(
            destino_id=destino_id,
            usuario_id=10,
            vendedor_id=20,
            tipo_atencion=VentaTipoAtencion.ATENDIDA,
            referencia_cliente=referencia,
        )
    )
    return venta


def open_session(db: Session, caja_id: int, cajero_id: int = 100) -> SesionCaja:
    sesion = SesionCaja(caja_id=caja_id, cajero_id=cajero_id, efectivo_inicial=Decimal("0.00"))
    db.add(sesion)
    db.commit()
    return sesion


def test_crear_venta_atendida_y_autoservicio_sin_vendedor(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)

    atendida = service.create(
        VentaCreate(destino_id=ids["destino_id"], vendedor_id=20, tipo_atencion=VentaTipoAtencion.ATENDIDA)
    )
    autoservicio = service.create(
        VentaCreate(destino_id=ids["destino_id"], vendedor_id=None, tipo_atencion=VentaTipoAtencion.AUTOSERVICIO)
    )

    assert atendida.vendedor_id == 20
    assert atendida.tipo_atencion == VentaTipoAtencion.ATENDIDA
    assert autoservicio.vendedor_id is None
    assert autoservicio.tipo_atencion == VentaTipoAtencion.AUTOSERVICIO


def test_referencia_cliente_se_modifica_solo_en_preparacion(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = create_sale(service, ids["destino_id"], referencia="Ana")

    updated = service.update_preparacion(venta.id, VentaUpdate(referencia_cliente="Ana mesa 3"))
    sent = service.enviar_a_caja(updated.id)

    assert updated.referencia_cliente == "Ana mesa 3"
    with pytest.raises(BusinessRuleViolation):
        service.update_preparacion(sent.id, VentaUpdate(referencia_cliente="Cambio tardio"))


def test_enviar_a_caja_asigna_numero_corto_monotonico_y_no_recicla_anulado(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    first = create_sale(service, ids["destino_id"], referencia="Ana")
    second = create_sale(service, ids["destino_id"], referencia="Beto")
    sent_first = service.enviar_a_caja(first.id, usuario_id=20)
    service.anular(sent_first.id, usuario_id=20)

    sent_second = service.enviar_a_caja(second.id, usuario_id=21)

    assert sent_first.numero_corto == 1
    assert sent_second.numero_corto == 2
    assert service.get(sent_first.id).numero_corto == 1
    assert service.get(sent_first.id).estado == VentaEstado.ANULADA


def test_destino_sin_secuencia_se_inicializa_al_primer_envio_y_continua(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    first = create_sale(service, ids["destino_id"], referencia="Primera")
    second = create_sale(service, ids["destino_id"], referencia="Segunda")
    third = create_sale(service, ids["destino_id"], referencia="Tercera")

    assert db_session.get(SecuenciaNumeroCortoVenta, ids["destino_id"]) is None

    sent_first = service.enviar_a_caja(first.id)
    sequence_after_first = db_session.get(SecuenciaNumeroCortoVenta, ids["destino_id"])
    service.anular(sent_first.id)
    sent_second = service.enviar_a_caja(second.id)
    sent_third = service.enviar_a_caja(third.id)

    assert sent_first.numero_corto == 1
    assert sequence_after_first is not None
    assert sequence_after_first.ultimo_numero == 3
    assert sent_second.numero_corto == 2
    assert sent_third.numero_corto == 3
    assert service.get(sent_first.id).numero_corto == 1


def test_listar_pendientes_filtra_por_destino_numero_y_referencia(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    ana = service.enviar_a_caja(create_sale(service, ids["destino_id"], referencia="Ana").id)
    service.enviar_a_caja(create_sale(service, ids["destino_id"], referencia="Beto").id)
    service.enviar_a_caja(create_sale(service, ids["otro_destino_id"], referencia="Ana otro local").id)

    by_destino = service.listar_pendientes_caja(destino_id=ids["destino_id"])
    by_number = service.listar_pendientes_caja(destino_id=ids["destino_id"], numero_corto=ana.numero_corto)
    by_reference = service.listar_pendientes_caja(destino_id=ids["destino_id"], referencia_cliente="ana")

    assert [venta.destino_id for venta in by_destino] == [ids["destino_id"], ids["destino_id"]]
    assert by_number == [ana]
    assert by_reference == [ana]


def test_captura_valida_bloquea_segunda_captura_y_saca_de_pendientes(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = service.enviar_a_caja(create_sale(service, ids["destino_id"]).id)

    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    sesion_2 = open_session(db_session, ids["caja_2_id"], cajero_id=101)

    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)

    assert captured.estado == VentaEstado.EN_COBRO
    assert captured.caja_captura_id == ids["caja_id"]
    assert captured.sesion_caja_id == sesion.id
    assert captured.capturada_at is not None
    assert service.listar_pendientes_caja(destino_id=ids["destino_id"]) == []
    with pytest.raises(BusinessRuleViolation) as error:
        service.capturar(venta.id, sesion_caja_id=sesion_2.id, usuario_id=101)
    assert error.value.code == SALE_ALREADY_CAPTURED


def test_caja_de_otro_destino_o_inactiva_no_captura(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = service.enviar_a_caja(create_sale(service, ids["destino_id"]).id)

    with pytest.raises(ValidationError):
        sesion_otro_destino = open_session(db_session, ids["caja_otro_destino_id"], cajero_id=100)
        service.capturar(venta.id, sesion_caja_id=sesion_otro_destino.id, usuario_id=100)
    with pytest.raises(ValidationError):
        sesion_inactiva = open_session(db_session, ids["caja_inactiva_id"], cajero_id=100)
        service.capturar(venta.id, sesion_caja_id=sesion_inactiva.id, usuario_id=100)


def test_liberar_conserva_numero_y_anulada_no_puede_capturarse(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = service.enviar_a_caja(create_sale(service, ids["destino_id"]).id)
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)

    released = service.liberar(captured.id, usuario_id=100)
    released_estado = released.estado
    released_numero_corto = released.numero_corto
    released_caja_captura_id = released.caja_captura_id
    released_sesion_caja_id = released.sesion_caja_id
    released_capturada_at = released.capturada_at
    anulada = service.anular(released.id, usuario_id=20)

    assert released_estado == VentaEstado.LISTA_PARA_COBRAR
    assert released_numero_corto == venta.numero_corto
    assert released_caja_captura_id is None
    assert released_sesion_caja_id is None
    assert released_capturada_at is None
    assert anulada.numero_corto == venta.numero_corto
    with pytest.raises(ValidationError):
        service.capturar(anulada.id, sesion_caja_id=sesion.id, usuario_id=100)


def test_lista_para_cobrar_no_permite_modificacion_normal_y_en_cobro_si(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = service.enviar_a_caja(create_sale(service, ids["destino_id"]).id)

    with pytest.raises(BusinessRuleViolation):
        service.add_item(
            venta.id,
            DetalleVentaCreate(
                variante_id=ids["variante_id"],
                cantidad=Decimal("1"),
                precio_unitario=Decimal("10.00"),
            ),
        )
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    captured = service.capturar(venta.id, sesion_caja_id=sesion.id, usuario_id=100)
    modified = service.add_item(
        captured.id,
        DetalleVentaCreate(
            variante_id=ids["variante_id"],
            cantidad=Decimal("1"),
            precio_unitario=Decimal("10.00"),
        ),
    )

    assert modified.total == Decimal("10.00")


def test_cerrada_sigue_inmutable(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = create_sale(service, ids["destino_id"])
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(variante_id=ids["variante_id"], cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),
    )
    service.add_pago(venta.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=venta.total))
    closed = service.finalizar(venta.id)

    with pytest.raises(BusinessRuleViolation):
        service.add_item(
            closed.id,
            DetalleVentaCreate(variante_id=ids["variante_id"], cantidad=Decimal("1"), precio_unitario=Decimal("10.00")),
        )


def test_eventos_operacion_venta_quedan_registrados(db_session: Session) -> None:
    ids = seed_salon(db_session)
    service = VentaService(db_session)
    venta = create_sale(service, ids["destino_id"])
    sent = service.enviar_a_caja(venta.id, usuario_id=20)
    sesion = open_session(db_session, ids["caja_id"], cajero_id=100)
    captured = service.capturar(sent.id, sesion_caja_id=sesion.id, usuario_id=100)
    released = service.liberar(captured.id, usuario_id=100)
    service.anular(released.id, usuario_id=20)

    events = db_session.scalars(select(EventoOperacionVenta).where(EventoOperacionVenta.venta_id == venta.id)).all()

    assert [event.tipo for event in events] == [
        EventoOperacionVentaTipo.CREACION,
        EventoOperacionVentaTipo.ENVIO_CAJA,
        EventoOperacionVentaTipo.CAPTURA,
        EventoOperacionVentaTipo.LIBERACION,
        EventoOperacionVentaTipo.ANULACION,
    ]
    assert events[2].caja_id == ids["caja_id"]
    assert events[2].sesion_caja_id == sesion.id
    assert events[3].sesion_caja_id == sesion.id


def test_restriccion_numero_corto_por_destino(db_session: Session) -> None:
    ids = seed_salon(db_session)
    first = Venta(numero_venta="V-DUP-1", destino_id=ids["destino_id"], numero_corto=7)
    second = Venta(numero_venta="V-DUP-2", destino_id=ids["destino_id"], numero_corto=7)
    db_session.add_all([first, second])

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_api_flujo_envio_captura_liberacion_y_conflicto(client: TestClient) -> None:
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_API_SALON", "nombre": "Local API Salon", "tipo": "LOCAL"},
    ).json()
    categoria = client.post("/api/categorias", json={"nombre": "Salon API"}).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "ART-API-SALON", "nombre": "API Salon", "categoria_id": categoria["id"]},
    ).json()
    client.post("/api/variantes", json={"articulo_id": articulo["id"], "codigo_barra": "7790000000999"})
    caja = Caja(destino_id=destino["id"], codigo="CAJA_API", nombre="Caja API")
    caja_2 = Caja(destino_id=destino["id"], codigo="CAJA_API_2", nombre="Caja API 2")
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    try:
        db.add_all([caja, caja_2])
        db.commit()
    finally:
        db.close()
        db_generator.close()

    venta = client.post(
        "/api/ventas",
        json={
            "destino_id": destino["id"],
            "vendedor_id": 20,
            "tipo_atencion": "ATENDIDA",
            "referencia_cliente": "Cliente API",
        },
    ).json()
    enviada = client.post(f"/api/ventas/{venta['id']}/enviar-a-caja", json={"usuario_id": 20})
    pendientes = client.get(
        "/api/ventas/caja/pendientes",
        params={"destino_id": destino["id"], "referencia_cliente": "Cliente"},
    )
    sesion = client.post(
        f"/api/cajas/{caja.id}/sesiones",
        json={"usuario_id": 100, "efectivo_inicial": "0.00"},
    ).json()["sesion"]
    sesion_2 = client.post(
        f"/api/cajas/{caja_2.id}/sesiones",
        json={"usuario_id": 101, "efectivo_inicial": "0.00"},
    ).json()["sesion"]
    capturada = client.post(
        f"/api/ventas/{venta['id']}/capturar",
        json={"sesion_caja_id": sesion["id"], "usuario_id": 100},
    )
    segunda_captura = client.post(
        f"/api/ventas/{venta['id']}/capturar",
        json={"sesion_caja_id": sesion_2["id"], "usuario_id": 101},
    )
    liberada = client.post(f"/api/ventas/{venta['id']}/liberar", json={"usuario_id": 100})

    assert enviada.status_code == 200
    assert enviada.json()["estado"] == "LISTA_PARA_COBRAR"
    assert enviada.json()["numero_corto"] == 1
    assert pendientes.status_code == 200
    assert [item["id"] for item in pendientes.json()] == [venta["id"]]
    assert capturada.status_code == 200
    assert capturada.json()["estado"] == "EN_COBRO"
    assert segunda_captura.status_code == 409
    assert segunda_captura.json()["detail"]["code"] == SALE_ALREADY_CAPTURED
    assert liberada.status_code == 200
    assert liberada.json()["estado"] == "LISTA_PARA_COBRAR"
    assert liberada.json()["numero_corto"] == 1
