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
    Caja,
    DestinoInventario,
    DestinoInventarioTipo,
    MovimientoCaja,
    MovimientoCajaTipo,
    PagoVenta,
    SesionCaja,
    SesionCajaEstado,
    Venta,
)
from backend.app.schemas.caja import MovimientoCajaCreate
from backend.app.services.caja_service import CajaService, impacto_movimiento_caja
from backend.app.services.exceptions import NotFoundError, ValidationError


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


def seed_cash_sessions(db: Session) -> dict[str, int]:
    destino = DestinoInventario(codigo="LOCAL_94", nombre="Local 9.4", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_94_1", nombre="Caja 9.4 1")
    caja_2 = Caja(destino=destino, codigo="CAJA_94_2", nombre="Caja 9.4 2")
    caja_inactiva = Caja(destino=destino, codigo="CAJA_94_INACTIVA", nombre="Caja inactiva", activa=False)
    sesion = SesionCaja(caja=caja, cajero_id=100, efectivo_inicial=Decimal("0.00"))
    otra_sesion = SesionCaja(caja=caja_2, cajero_id=101, efectivo_inicial=Decimal("0.00"))
    sesion_inactiva = SesionCaja(caja=caja_inactiva, cajero_id=100, efectivo_inicial=Decimal("0.00"))
    db.add_all([destino, caja, caja_2, caja_inactiva, sesion, otra_sesion, sesion_inactiva])
    db.commit()
    return {
        "sesion_id": sesion.id,
        "otra_sesion_id": otra_sesion.id,
        "sesion_inactiva_id": sesion_inactiva.id,
    }


def movement(
    tipo: MovimientoCajaTipo,
    importe: str = "100.00",
    *,
    usuario_id: int = 100,
    motivo: str | None = None,
) -> MovimientoCajaCreate:
    return MovimientoCajaCreate(
        usuario_id=usuario_id,
        tipo=tipo,
        importe=Decimal(importe),
        motivo=motivo,
    )


def test_ingreso_valido_con_y_sin_motivo(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    with_motivo = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.INGRESO, "30000.00", motivo=" refuerzo de cambio "),
    )
    without_motivo = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.INGRESO, "5000.00"),
    )

    assert with_motivo.tipo == MovimientoCajaTipo.INGRESO
    assert with_motivo.importe == Decimal("30000.00")
    assert with_motivo.motivo == "refuerzo de cambio"
    assert without_motivo.motivo is None
    assert with_motivo.sesion_caja_id == ids["sesion_id"]


def test_retiro_valido_y_no_requiere_motivo(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    with_motivo = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.RETIRO, "100000.00", motivo="A caja fuerte"),
    )
    without_motivo = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.RETIRO, "200000.00"),
    )

    assert with_motivo.tipo == MovimientoCajaTipo.RETIRO
    assert with_motivo.motivo == "A caja fuerte"
    assert without_motivo.motivo is None


def test_egreso_valido_exige_motivo_no_vacio(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    valid = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.EGRESO, "20000.00", motivo=" flete "),
    )

    assert valid.tipo == MovimientoCajaTipo.EGRESO
    assert valid.motivo == "flete"
    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.EGRESO))
    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.EGRESO, motivo=""))
    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.EGRESO, motivo="   "))


def test_importe_cero_y_negativo_se_rechazan() -> None:
    with pytest.raises(ValueError):
        movement(MovimientoCajaTipo.INGRESO, "0.00")
    with pytest.raises(ValueError):
        movement(MovimientoCajaTipo.RETIRO, "-1.00")


def test_validaciones_de_sesion_cajero_y_caja(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    with pytest.raises(NotFoundError):
        service.registrar_movimiento(999999, movement(MovimientoCajaTipo.INGRESO))
    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.RETIRO, usuario_id=101))

    sesion = db_session.get(SesionCaja, ids["sesion_id"])
    assert sesion is not None
    sesion.estado = SesionCajaEstado.CERRADA
    db_session.commit()
    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.INGRESO))

    with pytest.raises(ValidationError):
        service.registrar_movimiento(ids["sesion_inactiva_id"], movement(MovimientoCajaTipo.INGRESO))


def test_listado_de_movimientos_por_sesion_ordenado_y_sin_mezclar(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)
    first = service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.INGRESO, "10.00"))
    other = service.registrar_movimiento(ids["otra_sesion_id"], movement(MovimientoCajaTipo.INGRESO, "99.00", usuario_id=101))
    second = service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.RETIRO, "20.00"))
    third = service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.EGRESO, "30.00", motivo="flete"))

    listed = service.listar_movimientos(ids["sesion_id"])

    assert [item.id for item in listed] == [first.id, second.id, third.id]
    assert other.id not in {item.id for item in listed}
    assert all(item.sesion_caja_id == ids["sesion_id"] for item in listed)


def test_movimientos_no_crean_venta_ni_pago_y_son_append_only(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    for index in range(3):
        service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.RETIRO, str(index + 1)))

    assert db_session.scalars(select(Venta)).all() == []
    assert db_session.scalars(select(PagoVenta)).all() == []
    assert len(db_session.scalars(select(MovimientoCaja)).all()) == 3


def test_retiro_y_egreso_no_validan_saldo_suficiente(db_session: Session) -> None:
    ids = seed_cash_sessions(db_session)
    service = CajaService(db_session)

    retiro = service.registrar_movimiento(ids["sesion_id"], movement(MovimientoCajaTipo.RETIRO, "999999.00"))
    egreso = service.registrar_movimiento(
        ids["sesion_id"],
        movement(MovimientoCajaTipo.EGRESO, "999999.00", motivo="pago flete"),
    )

    assert retiro.importe == Decimal("999999.00")
    assert egreso.importe == Decimal("999999.00")


def test_impacto_de_movimientos_de_caja() -> None:
    assert impacto_movimiento_caja(MovimientoCajaTipo.INGRESO, Decimal("10.00")) == Decimal("10.00")
    assert impacto_movimiento_caja(MovimientoCajaTipo.RETIRO, Decimal("10.00")) == Decimal("-10.00")
    assert impacto_movimiento_caja(MovimientoCajaTipo.EGRESO, Decimal("10.00")) == Decimal("-10.00")
    with pytest.raises(ValidationError):
        impacto_movimiento_caja(MovimientoCajaTipo.AJUSTE, Decimal("10.00"))


def test_api_movimientos_caja_e_invalido_tipo(client: TestClient) -> None:
    destino = client.post(
        "/api/inventario/destinos",
        json={"codigo": "LOCAL_API_94", "nombre": "Local API 9.4", "tipo": "LOCAL"},
    ).json()
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    try:
        caja = Caja(destino_id=destino["id"], codigo="CAJA_API_94", nombre="Caja API 9.4")
        db.add(caja)
        db.commit()
    finally:
        db.close()
        db_generator.close()

    sesion = client.post(
        f"/api/cajas/{caja.id}/sesiones",
        json={"usuario_id": 100, "efectivo_inicial": "0.00"},
    ).json()["sesion"]
    created = client.post(
        f"/api/cajas/sesiones/{sesion['id']}/movimientos",
        json={"usuario_id": 100, "tipo": "EGRESO", "importe": "20.00", "motivo": "flete"},
    )
    invalid = client.post(
        f"/api/cajas/sesiones/{sesion['id']}/movimientos",
        json={"usuario_id": 100, "tipo": "PAGO", "importe": "20.00", "motivo": "flete"},
    )
    listed = client.get(f"/api/cajas/sesiones/{sesion['id']}/movimientos")

    assert created.status_code == 201
    assert created.json()["sesion_caja_id"] == sesion["id"]
    assert created.json()["tipo"] == "EGRESO"
    assert invalid.status_code == 422
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
