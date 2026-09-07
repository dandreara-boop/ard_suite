from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.business.inventory import (
    INVENTORY_DESTINATION_INVALID,
    INVENTORY_EVENT_ALREADY_PROCESSED,
    INVENTORY_VARIANT_INVALID,
    INVENTORY_ZERO_MOVEMENT,
)
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    Articulo,
    Categoria,
    DestinoInventario,
    DestinoInventarioTipo,
    MovimientoStock,
    MovimientoStockTipo,
    StockActual,
    StockEstado,
    Variante,
)
from backend.app.schemas import AjusteStockCreate, MovimientoStockCreate
from backend.app.services.exceptions import BusinessRuleViolation
from backend.app.services.inventory_service import InventoryService


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


def seed_inventory_base(db: Session) -> dict[str, int]:
    categoria = Categoria(nombre="Remeras")
    articulo = Articulo(codigo="ART-INV", nombre="Remera inventario", categoria=categoria)
    variante = Variante(articulo=articulo, codigo_barra="7790000000010")
    destino = DestinoInventario(
        codigo="LOCAL_CENTRO",
        nombre="Local Centro",
        tipo=DestinoInventarioTipo.LOCAL,
    )
    db.add_all([categoria, articulo, variante, destino])
    db.commit()
    return {"variante_id": variante.id, "destino_id": destino.id}


def movement(ids: dict[str, int], cantidad: int, global_id: str = "evt-1") -> MovimientoStockCreate:
    return MovimientoStockCreate(
        global_id=global_id,
        variante_id=ids["variante_id"],
        destino_id=ids["destino_id"],
        estado=StockEstado.DISPONIBLE,
        cantidad=cantidad,
        tipo=MovimientoStockTipo.RECEPCION_COMPRA if cantidad > 0 else MovimientoStockTipo.VENTA,
        origen_tipo="TEST",
        origen_id=global_id,
    )


def get_stock(db: Session) -> StockActual:
    return db.scalar(select(StockActual))  # type: ignore[return-value]


def test_movimiento_positivo_genera_stock_10(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)

    outcome = InventoryService(db_session).registrar_movimiento(movement(ids, 10))

    assert outcome.applied is True
    assert outcome.stock is not None
    assert outcome.stock.cantidad == 10


def test_movimiento_negativo_deja_stock_7(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    service = InventoryService(db_session)

    service.registrar_movimiento(movement(ids, 10, "evt-1"))
    outcome = service.registrar_movimiento(movement(ids, -3, "evt-2"))

    assert outcome.stock is not None
    assert outcome.stock.cantidad == 7


def test_movimiento_que_deja_stock_negativo_es_aceptado(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)

    outcome = InventoryService(db_session).registrar_movimiento(movement(ids, -1))

    assert outcome.stock is not None
    assert outcome.stock.cantidad == -1


def test_global_id_repetido_no_aplica_dos_veces(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    service = InventoryService(db_session)

    first = service.registrar_movimiento(movement(ids, 10, "evt-repeat"))
    second = service.registrar_movimiento(movement(ids, 10, "evt-repeat"))

    assert first.applied is True
    assert second.applied is False
    assert second.rule_result.code == INVENTORY_EVENT_ALREADY_PROCESSED
    assert get_stock(db_session).cantidad == 10
    assert len(db_session.scalars(select(MovimientoStock)).all()) == 1


def test_movimiento_cero_es_rechazado(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)

    with pytest.raises(BusinessRuleViolation) as error:
        InventoryService(db_session).registrar_movimiento(movement(ids, 0))

    assert error.value.code == INVENTORY_ZERO_MOVEMENT


def test_variante_inexistente_es_rechazada(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    ids["variante_id"] = 9999

    with pytest.raises(BusinessRuleViolation) as error:
        InventoryService(db_session).registrar_movimiento(movement(ids, 1))

    assert error.value.code == INVENTORY_VARIANT_INVALID


def test_destino_inexistente_es_rechazado(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    ids["destino_id"] = 9999

    with pytest.raises(BusinessRuleViolation) as error:
        InventoryService(db_session).registrar_movimiento(movement(ids, 1))

    assert error.value.code == INVENTORY_DESTINATION_INVALID


def test_ajuste_10_a_8_genera_menos_2(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)

    outcome = InventoryService(db_session).ajustar_stock(
        AjusteStockCreate(
            global_id="ajuste-1",
            variante_id=ids["variante_id"],
            destino_id=ids["destino_id"],
            stock_teorico=10,
            stock_contado=8,
            motivo="conteo",
        )
    )

    assert outcome.movimiento.tipo == MovimientoStockTipo.AJUSTE_NEGATIVO
    assert outcome.movimiento.cantidad == -2


def test_ajuste_8_a_12_genera_mas_4(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)

    outcome = InventoryService(db_session).ajustar_stock(
        AjusteStockCreate(
            global_id="ajuste-2",
            variante_id=ids["variante_id"],
            destino_id=ids["destino_id"],
            stock_teorico=8,
            stock_contado=12,
            motivo="conteo",
        )
    )

    assert outcome.movimiento.tipo == MovimientoStockTipo.AJUSTE_POSITIVO
    assert outcome.movimiento.cantidad == 4


def test_multiples_movimientos_conservan_historial(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    service = InventoryService(db_session)

    service.registrar_movimiento(movement(ids, 10, "evt-1"))
    service.registrar_movimiento(movement(ids, -3, "evt-2"))
    service.registrar_movimiento(movement(ids, -20, "evt-3"))

    movimientos = service.list_movimientos(variante_id=ids["variante_id"])
    assert [item.cantidad for item in movimientos] == [10, -3, -20]


def test_stock_actual_coincide_con_suma_de_movimientos(db_session: Session) -> None:
    ids = seed_inventory_base(db_session)
    service = InventoryService(db_session)

    service.registrar_movimiento(movement(ids, 10, "evt-1"))
    service.registrar_movimiento(movement(ids, -3, "evt-2"))
    service.registrar_movimiento(movement(ids, -20, "evt-3"))

    assert get_stock(db_session).cantidad == -13
    assert service.stock.movement_sum(ids["variante_id"], ids["destino_id"], StockEstado.DISPONIBLE) == -13


def test_rollback_si_falla_actualizacion_de_saldo(db_session: Session, monkeypatch) -> None:
    ids = seed_inventory_base(db_session)
    service = InventoryService(db_session)

    def fail_stock_update(*args, **kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(service, "_apply_stock_delta", fail_stock_update)

    with pytest.raises(RuntimeError):
        service.registrar_movimiento(movement(ids, 10, "evt-rollback"))

    assert db_session.scalars(select(MovimientoStock)).all() == []
    assert db_session.scalars(select(StockActual)).all() == []


def test_inventory_api_positive_negative_negative_stock_idempotency_adjustment_and_history(
    client: TestClient,
) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Remeras"}).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "ART-API", "nombre": "Remera API", "categoria_id": categoria["id"]},
    ).json()
    variante = client.post(
        "/api/variantes",
        json={"articulo_id": articulo["id"], "codigo_barra": "7790000000096"},
    ).json()
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_CENTRO", "nombre": "Local Centro", "tipo": "LOCAL"},
    ).json()

    positive = client.post(
        "/api/inventario/movimientos",
        json={
            "global_id": "api-1",
            "variante_id": variante["id"],
            "destino_id": destino["id"],
            "estado": "DISPONIBLE",
            "cantidad": 10,
            "tipo": "RECEPCION_COMPRA",
            "origen_tipo": "QA",
        },
    )
    negative = client.post(
        "/api/inventario/movimientos",
        json={
            "global_id": "api-2",
            "variante_id": variante["id"],
            "destino_id": destino["id"],
            "estado": "DISPONIBLE",
            "cantidad": -15,
            "tipo": "VENTA",
            "origen_tipo": "QA",
        },
    )
    repeated = client.post(
        "/api/inventario/movimientos",
        json={
            "global_id": "api-2",
            "variante_id": variante["id"],
            "destino_id": destino["id"],
            "estado": "DISPONIBLE",
            "cantidad": -15,
            "tipo": "VENTA",
            "origen_tipo": "QA",
        },
    )
    ajuste = client.post(
        "/api/inventario/ajustes",
        json={
            "global_id": "api-ajuste",
            "variante_id": variante["id"],
            "destino_id": destino["id"],
            "estado": "DISPONIBLE",
            "stock_teorico": -5,
            "stock_contado": -3,
            "motivo": "conteo QA",
        },
    )
    stock = client.get(f"/api/inventario/stock/{variante['id']}").json()
    historial = client.get("/api/inventario/movimientos").json()

    assert positive.status_code == 201
    assert negative.status_code == 201
    assert negative.json()["stock"]["cantidad"] == -5
    assert repeated.status_code == 201
    assert repeated.json()["applied"] is False
    assert repeated.json()["rule_result"]["code"] == INVENTORY_EVENT_ALREADY_PROCESSED
    assert ajuste.status_code == 201
    assert ajuste.json()["movimiento"]["cantidad"] == 2
    assert stock[0]["cantidad"] == -3
    assert [item["global_id"] for item in historial] == ["api-1", "api-2", "api-ajuste"]
