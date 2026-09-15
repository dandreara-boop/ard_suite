from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.business.sales import SALE_ALREADY_CLOSED, SALE_EMPTY, SALE_PAYMENT_INSUFFICIENT
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    Articulo,
    Categoria,
    DestinoInventario,
    DestinoInventarioTipo,
    EventoPendiente,
    EventoPendienteEstado,
    MovimientoStock,
    MovimientoStockTipo,
    StockActual,
    Variante,
    VentaEstado,
)
from backend.app.schemas.venta import DetalleVentaCreate, PagoVentaCreate, VentaCreate
from backend.app.services.exceptions import BusinessRuleViolation
from backend.app.services.inventory_service import InventoryService
from backend.app.services.venta_service import VENTA_FINALIZADA, VentaService, sale_movement_global_id


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


def seed_sales_base(db: Session) -> dict[str, int]:
    categoria = Categoria(nombre="Remeras")
    articulo = Articulo(codigo="ART-SALE", nombre="Remera venta", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000218")
    destino = DestinoInventario(
        codigo="LOCAL_CENTRO",
        nombre="Local Centro",
        tipo=DestinoInventarioTipo.LOCAL,
    )
    db.add_all([categoria, articulo, variante, destino])
    db.commit()
    return {
        "articulo_id": articulo.id,
        "variante_id": variante.id,
        "destino_id": destino.id,
    }


def create_ready_sale(service: VentaService, ids: dict[str, int], cantidad: str = "2") -> int:
    venta = service.create(VentaCreate(destino_id=ids["destino_id"]))
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(
            variante_id=ids["variante_id"],
            cantidad=Decimal(cantidad),
            precio_unitario=Decimal("100.00"),
        ),
    )
    venta = service.add_pago(venta.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=venta.total))
    return venta.id


def test_crear_venta_agregar_item_pago_y_finalizar_genera_evento(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)

    venta_id = create_ready_sale(service, ids)
    venta = service.finalizar(venta_id)

    eventos = db_session.scalars(select(EventoPendiente)).all()
    assert venta.estado == VentaEstado.CERRADA
    assert venta.cerrada_at is not None
    assert venta.total == Decimal("200.00")
    assert len(eventos) == 1
    assert eventos[0].tipo == VENTA_FINALIZADA
    assert eventos[0].estado == EventoPendienteEstado.PENDIENTE
    assert eventos[0].payload["venta_id"] == venta.id
    assert eventos[0].payload["venta_global_id"] == venta.global_id
    assert eventos[0].payload["destino_id"] == ids["destino_id"]


def test_venta_sin_items_es_rechazada(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta = service.create(VentaCreate(destino_id=ids["destino_id"]))

    with pytest.raises(BusinessRuleViolation) as error:
        service.finalizar(venta.id)

    assert error.value.code == SALE_EMPTY
    assert db_session.scalars(select(EventoPendiente)).all() == []


def test_pago_insuficiente_es_rechazado(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta = service.create(VentaCreate(destino_id=ids["destino_id"]))
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(
            variante_id=ids["variante_id"],
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100.00"),
        ),
    )
    service.add_pago(venta.id, PagoVentaCreate(medio_pago="EFECTIVO", importe=Decimal("99.00")))

    with pytest.raises(BusinessRuleViolation) as error:
        service.finalizar(venta.id)

    assert error.value.code == SALE_PAYMENT_INSUFFICIENT
    assert db_session.scalars(select(EventoPendiente)).all() == []


def test_doble_finalizacion_no_duplica_evento(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta_id = create_ready_sale(service, ids)

    service.finalizar(venta_id)
    with pytest.raises(BusinessRuleViolation) as error:
        service.finalizar(venta_id)

    assert error.value.code == SALE_ALREADY_CLOSED
    assert len(db_session.scalars(select(EventoPendiente)).all()) == 1


def test_procesar_evento_genera_movimiento_stock_negativo_e_idempotente(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta_id = create_ready_sale(service, ids, cantidad="3")
    venta = service.finalizar(venta_id)
    detalle_id = venta.detalles[0].id

    first = service.procesar_eventos_pendientes()
    stock_after_first = db_session.scalar(select(StockActual))
    second = service.procesar_eventos_pendientes()
    stock_after_second = db_session.scalar(select(StockActual))
    movimientos = db_session.scalars(select(MovimientoStock)).all()
    evento = db_session.scalar(select(EventoPendiente))

    assert first.procesados == 1
    assert first.errores == 0
    assert second.procesados == 0
    assert stock_after_first is not None
    assert stock_after_first.cantidad == -3
    assert stock_after_second is not None
    assert stock_after_second.cantidad == -3
    assert len(movimientos) == 1
    assert movimientos[0].global_id == sale_movement_global_id(venta.global_id, detalle_id)
    assert len(movimientos[0].global_id) <= MovimientoStock.__table__.c.global_id.type.length
    assert movimientos[0].tipo == MovimientoStockTipo.VENTA
    assert movimientos[0].cantidad == -3
    assert evento is not None
    assert evento.estado == EventoPendienteEstado.PROCESADO
    assert evento.processed_at is not None


def test_reprocesar_evento_error_no_duplica_movimiento_existente(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta_id = create_ready_sale(service, ids, cantidad="1")
    service.finalizar(venta_id)
    service.procesar_eventos_pendientes()
    evento = db_session.scalar(select(EventoPendiente))
    assert evento is not None
    evento.estado = EventoPendienteEstado.ERROR
    evento.processed_at = None
    db_session.commit()

    result = service.procesar_eventos_pendientes()

    assert result.procesados == 1
    assert len(db_session.scalars(select(MovimientoStock)).all()) == 1
    assert db_session.scalar(select(StockActual)).cantidad == -1  # type: ignore[union-attr]


def test_fallo_de_procesamiento_conserva_evento_y_registra_error(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta_id = create_ready_sale(service, ids)
    service.finalizar(venta_id)
    original_registrar_movimiento = InventoryService.registrar_movimiento

    def fail_inventory(*args, **kwargs):
        raise RuntimeError("fallo inventario")

    monkeypatch.setattr(InventoryService, "registrar_movimiento", fail_inventory)
    result = service.procesar_eventos_pendientes()
    evento = db_session.scalar(select(EventoPendiente))

    assert result.errores == 1
    assert evento is not None
    assert evento.estado == EventoPendienteEstado.ERROR
    assert evento.intentos == 1
    assert evento.ultimo_error == "fallo inventario"
    assert evento.processed_at is None
    assert db_session.scalars(select(MovimientoStock)).all() == []
    assert db_session.scalars(select(StockActual)).all() == []

    monkeypatch.setattr(InventoryService, "registrar_movimiento", original_registrar_movimiento)
    retry = service.procesar_eventos_pendientes()
    evento = db_session.scalar(select(EventoPendiente))

    assert retry.procesados == 1
    assert retry.errores == 0
    assert evento is not None
    assert evento.estado == EventoPendienteEstado.PROCESADO
    assert evento.intentos == 2
    assert evento.processed_at is not None
    assert evento.ultimo_error is None
    assert len(db_session.scalars(select(MovimientoStock)).all()) == 1
    assert db_session.scalar(select(StockActual)).cantidad == -2  # type: ignore[union-attr]


def test_evento_procesado_no_se_vuelve_a_aplicar(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta_id = create_ready_sale(service, ids, cantidad="2")
    service.finalizar(venta_id)
    service.procesar_eventos_pendientes()

    result = service.procesar_eventos_pendientes()

    assert result.procesados == 0
    assert result.errores == 0
    assert len(db_session.scalars(select(MovimientoStock)).all()) == 1
    assert db_session.scalar(select(StockActual)).cantidad == -2  # type: ignore[union-attr]


def test_global_id_de_movimiento_venta_es_uuid5_deterministico_y_compatible() -> None:
    venta_global_id = "2508f516-b7d5-4098-9e21-555914373c42"
    detalle_id = 1
    same = sale_movement_global_id(venta_global_id, detalle_id)
    repeated = sale_movement_global_id(venta_global_id, detalle_id)
    different = sale_movement_global_id(venta_global_id, detalle_id + 1)

    assert same == repeated
    assert same != different
    assert UUID(same).version == 5
    assert len(same) == 36
    assert len(same) <= MovimientoStock.__table__.c.global_id.type.length


def test_snapshot_detalle_no_cambia_si_cambia_catalogo(db_session: Session) -> None:
    ids = seed_sales_base(db_session)
    service = VentaService(db_session)
    venta = service.create(VentaCreate(destino_id=ids["destino_id"]))
    venta = service.add_item(
        venta.id,
        DetalleVentaCreate(
            variante_id=ids["variante_id"],
            cantidad=Decimal("1"),
            precio_unitario=Decimal("50.00"),
        ),
    )
    articulo = db_session.get(Articulo, ids["articulo_id"])
    assert articulo is not None
    articulo.codigo = "ART-CAMBIADO"
    articulo.nombre = "Nombre cambiado"
    db_session.commit()

    venta = service.get(venta.id)

    assert venta.detalles[0].codigo_articulo == "ART-SALE"
    assert venta.detalles[0].codigo_barra == "7790000000218"
    assert venta.detalles[0].descripcion == "Remera venta"


def test_sales_api_flow_and_event_processor(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Remeras"}).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "ART-API-SALE", "nombre": "Remera API venta", "categoria_id": categoria["id"]},
    ).json()
    variante = client.post(
        "/api/variantes",
        json={"articulo_id": articulo["id"], "codigo_barra": "7790000000300"},
    ).json()
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_CENTRO", "nombre": "Local Centro", "tipo": "LOCAL"},
    ).json()

    venta = client.post("/api/ventas", json={"destino_id": destino["id"]}).json()
    venta = client.post(
        f"/api/ventas/{venta['id']}/items",
        json={"variante_id": variante["id"], "cantidad": "2", "precio_unitario": "10.00"},
    ).json()
    venta = client.post(
        f"/api/ventas/{venta['id']}/pagos",
        json={"medio_pago": "EFECTIVO", "importe": "20.00"},
    ).json()
    finalizar = client.post(f"/api/ventas/{venta['id']}/finalizar")
    eventos = client.get("/api/ventas/eventos/pendientes").json()
    procesar = client.post("/api/ventas/eventos/procesar", json={"limit": 10})
    stock = client.get(f"/api/inventario/stock/{variante['id']}").json()

    assert finalizar.status_code == 200
    assert finalizar.json()["estado"] == "CERRADA"
    assert len(eventos) == 1
    assert eventos[0]["tipo"] == VENTA_FINALIZADA
    assert eventos[0]["estado"] == "PENDIENTE"
    assert procesar.status_code == 200
    assert procesar.json() == {"procesados": 1, "errores": 0}
    assert stock[0]["cantidad"] == -2
