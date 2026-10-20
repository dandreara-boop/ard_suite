from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import importlib.util
from pathlib import Path
import sqlite3
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    ArqueoCaja, ArqueoCajaTipo, Caja, DestinoInventario, DestinoInventarioTipo,
    MovimientoCaja, PagoVenta, SesionCaja, SesionCajaEstado,
    SolicitudCorreccionArqueo, SolicitudCorreccionArqueoEstado, Venta,
)
from backend.app.schemas.caja import CerrarSesionCajaRequest, SolicitarCorreccionArqueoRequest
from backend.app.services.caja_service import CajaService, _is_duplicate_correction
from backend.app.services.exceptions import ConflictError, NotFoundError, ValidationError


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        yield db
    engine.dispose()


def seed(db: Session) -> tuple[int, int, int]:
    destino = DestinoInventario(codigo="LOCAL_95CA", nombre="Local", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_95CA", nombre="Caja")
    sesion = SesionCaja(caja=caja, cajero_id=100, efectivo_inicial=Decimal("100.00"))
    db.add(sesion)
    db.commit()
    closed = CajaService(db).cerrar_sesion(
        sesion.id, CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("90.00")),
    )
    return caja.id, sesion.id, closed.arqueo_id


def request(**changes) -> SolicitarCorreccionArqueoRequest:
    return SolicitarCorreccionArqueoRequest(**{
        "usuario_id": 100, "importe_corregido": Decimal("95.00"), "motivo": "  Efectivo omitido  ", **changes,
    })


def count_requests(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(SolicitudCorreccionArqueo))


def test_solicitud_valida_conserva_original_y_no_tiene_resolucion(db_session):
    _, _, arqueo_id = seed(db_session)
    response = CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    row = db_session.get(SolicitudCorreccionArqueo, response.solicitud_id)
    assert response.arqueo_id == arqueo_id
    assert response.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
    assert response.conteo_original == Decimal("90.00")
    assert response.conteo_corregido_propuesto == Decimal("95.00")
    assert response.solicitada_at is not None
    assert row.motivo == "Efectivo omitido"
    assert row.cajero_solicitante_id == 100
    assert row.supervisor_id is row.resuelta_at is row.observacion is None


@pytest.mark.parametrize("case,error", [
    ("cajero", ValidationError), ("abierta", ConflictError),
    ("inexistente", NotFoundError), ("control", ValidationError),
])
def test_contextos_invalidos(db_session, case, error):
    _, sesion_id, arqueo_id = seed(db_session)
    data = request()
    if case == "cajero":
        data = request(usuario_id=101)
    elif case == "abierta":
        db_session.get(SesionCaja, sesion_id).estado = SesionCajaEstado.ABIERTA
    elif case == "control":
        db_session.get(ArqueoCaja, arqueo_id).tipo = ArqueoCajaTipo.CONTROL
    else:
        arqueo_id += 1000
    db_session.commit()
    with pytest.raises(error):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, data)
    assert count_requests(db_session) == 0


@pytest.mark.parametrize("cerrar", [False, True])
def test_sesion_posterior_vence_plazo_incluso_cerrada_y_con_fecha_empatada(db_session, cerrar):
    caja_id, sesion_id, arqueo_id = seed(db_session)
    service = CajaService(db_session)
    posterior = service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0")).sesion
    # Same timestamps intentionally: only the local increasing ID determines order.
    db_session.get(SesionCaja, posterior.id).abierta_at = db_session.get(SesionCaja, sesion_id).abierta_at
    db_session.commit()
    if cerrar:
        service.cerrar_sesion(posterior.id, CerrarSesionCajaRequest(usuario_id=101, efectivo_final_declarado=0))
    with pytest.raises(ConflictError, match="plazo"):
        service.solicitar_correccion_arqueo(arqueo_id, request())
    assert count_requests(db_session) == 0


def test_nueva_sesion_en_otra_caja_no_vence_plazo(db_session):
    caja_id, _, arqueo_id = seed(db_session)
    otra = Caja(destino_id=db_session.get(Caja, caja_id).destino_id, codigo="OTRA", nombre="Otra")
    db_session.add(otra)
    db_session.commit()
    CajaService(db_session).abrir_sesion(otra.id, cajero_id=100, efectivo_inicial=Decimal("0"))
    CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    assert count_requests(db_session) == 1


@pytest.mark.parametrize("estado", list(SolicitudCorreccionArqueoEstado))
def test_solicitud_previa_en_cualquier_estado_impide_otra(db_session, estado):
    _, _, arqueo_id = seed(db_session)
    response = CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    db_session.get(SolicitudCorreccionArqueo, response.solicitud_id).estado = estado
    db_session.commit()
    with pytest.raises(ConflictError, match="ya tiene"):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    assert count_requests(db_session) == 1


@pytest.mark.parametrize("amount", ["-1", "10000000000", "NaN", "Infinity", "-Infinity", "0.001"])
def test_importes_invalidos_en_schema_y_servicio(db_session, amount):
    _, _, arqueo_id = seed(db_session)
    with pytest.raises(SchemaValidationError):
        request(importe_corregido=amount)
    bypassed = SolicitarCorreccionArqueoRequest.model_construct(
        usuario_id=100, importe_corregido=Decimal(amount), motivo="Motivo",
    )
    with pytest.raises(ValidationError):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, bypassed)
    assert count_requests(db_session) == 0


def test_importe_cero_es_valido(db_session):
    _, _, arqueo_id = seed(db_session)
    response = CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request(importe_corregido=0))
    assert response.conteo_corregido_propuesto == Decimal("0.00")


@pytest.mark.parametrize("motivo", ["", "   ", "\n\t"])
def test_motivo_vacio_en_schema_y_servicio(db_session, motivo):
    _, _, arqueo_id = seed(db_session)
    with pytest.raises(SchemaValidationError):
        request(motivo=motivo)
    data = SolicitarCorreccionArqueoRequest.model_construct(
        usuario_id=100, importe_corregido=Decimal("1"), motivo=motivo,
    )
    with pytest.raises(ValidationError, match="motivo"):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, data)


def snapshot(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def test_inmutabilidad_y_ausencia_de_efectos_automaticos(db_session):
    _, sesion_id, arqueo_id = seed(db_session)
    arqueo = db_session.get(ArqueoCaja, arqueo_id)
    sesion = db_session.get(SesionCaja, sesion_id)
    before = snapshot(arqueo), snapshot(sesion)
    totals = [db_session.scalar(select(func.count()).select_from(m)) for m in [Venta, PagoVenta, MovimientoCaja]]
    CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    db_session.expire_all()
    assert (snapshot(arqueo), snapshot(sesion)) == before
    assert [db_session.scalar(select(func.count()).select_from(m)) for m in [Venta, PagoVenta, MovimientoCaja]] == totals


def test_pendiente_permite_siguiente_turno_y_caja_inactiva_no_agrega_requisito(db_session):
    caja_id, _, arqueo_id = seed(db_session)
    caja = db_session.get(Caja, caja_id)
    caja.activa = False
    db_session.commit()
    CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    caja.activa = True
    db_session.commit()
    CajaService(db_session).abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("10"))
    assert count_requests(db_session) == 1


def test_rollback_despues_de_flush_y_reintento(db_session, monkeypatch):
    _, _, arqueo_id = seed(db_session)
    original_commit = db_session.commit
    def fail_commit():
        assert count_requests(db_session) == 1
        raise RuntimeError("fallo antes de confirmar")
    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    assert count_requests(db_session) == 0
    monkeypatch.setattr(db_session, "commit", original_commit)
    CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    assert count_requests(db_session) == 1


def test_unique_en_bd_rechaza_insercion_directa(db_session):
    _, _, arqueo_id = seed(db_session)
    CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    db_session.add(SolicitudCorreccionArqueo(
        arqueo_id=arqueo_id, conteo_original=90, conteo_corregido_propuesto=99,
        cajero_solicitante_id=100, motivo="Duplicado",
    ))
    with pytest.raises(IntegrityError) as caught:
        db_session.commit()
    assert _is_duplicate_correction(caught.value)
    db_session.rollback()
    assert count_requests(db_session) == 1


@pytest.mark.parametrize("specific", [True, False])
def test_integrity_error_solo_traduce_la_constraint_correcta_y_hace_rollback(db_session, monkeypatch, specific):
    _, _, arqueo_id = seed(db_session)
    key = "uq_solicitudes_correccion_arqueo_arqueo_id" if specific else "ix_solicitudes_correccion_arqueo_global_id"
    failure = IntegrityError("INSERT", {}, Exception(1062, f"Duplicate entry for key '{key}'"))
    original_flush = db_session.flush
    def failing_insert(*args, **kwargs):
        if any(isinstance(row, SolicitudCorreccionArqueo) for row in db_session.new):
            raise failure
        return original_flush(*args, **kwargs)
    monkeypatch.setattr(db_session, "flush", failing_insert)
    with pytest.raises(ConflictError if specific else IntegrityError):
        CajaService(db_session).solicitar_correccion_arqueo(arqueo_id, request())
    assert not db_session.new
    assert count_requests(db_session) == 0


def test_otras_integridades_sqlite_no_se_confunden():
    error = sqlite3.IntegrityError("NOT NULL constraint failed: solicitudes_correccion_arqueo.motivo")
    assert not _is_duplicate_correction(IntegrityError("INSERT", {}, error))


def test_contrato_endpoint_y_errores(db_session):
    _, _, arqueo_id = seed(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            url = f"/api/cajas/arqueos/{arqueo_id}/solicitar-correccion"
            payload = {"usuario_id": 100, "importe_corregido": "95.00", "motivo": "Efectivo omitido"}
            assert client.post(url, json={**payload, "usuario_id": 101}).status_code == 422
            assert client.post(url, json={**payload, "motivo": "  "}).status_code == 422
            assert client.post(url, json={k: v for k, v in payload.items() if k != "importe_corregido"}).status_code == 422
            assert client.post(url, json={k: v for k, v in payload.items() if k != "motivo"}).status_code == 422
            assert client.post("/api/cajas/arqueos/999999/solicitar-correccion", json=payload).status_code == 404
            response = client.post(url, json=payload)
            assert response.status_code == 201
            body = response.json()
            assert set(body) == {"solicitud_id", "arqueo_id", "estado", "conteo_original", "conteo_corregido_propuesto", "solicitada_at"}
            assert body["estado"] == "PENDIENTE"
            assert body["conteo_original"] == "90.00"
            assert body["conteo_corregido_propuesto"] == "95.00"
            assert body["solicitada_at"]
            assert client.post(url, json=payload).status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(params=["sqlite", "mariadb"])
def concurrent_sessions(request, tmp_path):
    admin = None
    database = None
    if request.param == "sqlite":
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / '95ca.db'}", connect_args={"check_same_thread": False})
    else:
        # Never create/drop tables in the configured operational database.
        # A fresh random database is the only permitted MariaDB test target.
        database = "ard_suite_test_95ca_" + uuid4().hex
        admin = create_engine(settings.sqlalchemy_database_uri, connect_args={"connect_timeout": 3})
        try:
            with admin.connect() as connection:
                connection.execute(text(f"CREATE DATABASE `{database}`"))
        except OperationalError as error:
            admin.dispose()
            pytest.skip(f"MariaDB aislada no disponible (codigo {error.orig.args[0]})")
        engine = create_engine(admin.url.set(database=database), isolation_level="REPEATABLE READ")
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False), engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.execute(text(f"DROP DATABASE `{database}`"))
            admin.dispose()


def test_solicitudes_simultaneas_dejan_una_sola(concurrent_sessions):
    factory, _ = concurrent_sessions
    with factory() as db:
        _, _, arqueo_id = seed(db)
    barrier = Barrier(2)
    def attempt():
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                CajaService(db).solicitar_correccion_arqueo(arqueo_id, request())
                return "created"
            except ConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == ["conflict", "created"]
    with factory() as db:
        assert count_requests(db) == 1


@pytest.mark.parametrize("first", ["solicitud", "apertura"])
def test_carrera_apertura_solicitud_respeta_orden_y_snapshot(concurrent_sessions, first):
    factory, engine = concurrent_sessions
    with factory() as db:
        caja_id, _, arqueo_id = seed(db)
    started, release, second_attempted = Event(), Event(), Event()
    def primary():
        with factory() as db:
            def before_commit(session):
                started.set()
                assert release.wait(timeout=10)
            event.listen(db, "before_commit", before_commit, once=True)
            if first == "solicitud":
                CajaService(db).solicitar_correccion_arqueo(arqueo_id, request())
            else:
                CajaService(db).abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0"))
    def secondary():
        with factory() as db:
            # Force a prior REPEATABLE READ snapshot and preloaded identities.
            db.get(ArqueoCaja, arqueo_id)
            db.scalar(select(SesionCaja.id).where(SesionCaja.caja_id == caja_id))
            if engine.dialect.name == "sqlite":
                db.rollback()  # BEGIN IMMEDIATE must be the first SQLite transaction statement.
            second_attempted.set()
            try:
                if first == "solicitud":
                    CajaService(db).abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0"))
                else:
                    CajaService(db).solicitar_correccion_arqueo(arqueo_id, request())
                return "ok"
            except ConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(primary)
        try:
            assert started.wait(timeout=10)
            b = executor.submit(secondary)
            assert second_attempted.wait(timeout=10)
        finally:
            release.set()
        a.result(timeout=20)
        assert b.result(timeout=20) == ("ok" if first == "solicitud" else "conflict")
    with factory() as db:
        assert count_requests(db) == (1 if first == "solicitud" else 0)
        assert db.scalar(select(func.count()).select_from(SesionCaja)) == 2


@pytest.mark.parametrize("historicas,duplicados", [(1, 0), (2, 1), (0, 0)])
def test_migracion_prevalida_antes_de_ddl_sin_aplicarla(monkeypatch, historicas, duplicados):
    path = Path(__file__).parents[1] / "alembic/versions/20261016_0011_solicitud_correccion_arqueo.py"
    spec = importlib.util.spec_from_file_location("migration_95ca", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    bind = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), scalar=Mock(side_effect=[historicas, duplicados]))
    execute = Mock()
    monkeypatch.setattr(migration, "op", SimpleNamespace(
        get_context=lambda: SimpleNamespace(as_sql=False), get_bind=lambda: bind, execute=execute,
    ))
    if historicas or duplicados:
        with pytest.raises(RuntimeError, match="antes de DDL"):
            migration.upgrade()
        execute.assert_not_called()
    else:
        migration.upgrade()
        execute.assert_called_once()
        sql = str(execute.call_args.args[0])
        assert "motivo TEXT NOT NULL" in sql and "UNIQUE (arqueo_id)" in sql
    assert bind.scalar.call_count == 2
