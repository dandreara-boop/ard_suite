"""Transaction recovery with SQLite + injected driver errors, never MariaDB."""
from decimal import Decimal
from unittest.mock import Mock

from pymysql.err import OperationalError as DriverOperationalError
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.app.models import (
    ArqueoCaja, MovimientoCaja, PagoVenta, SesionCaja, SesionCajaEstado,
    SolicitudCorreccionArqueo, SolicitudCorreccionArqueoEstado, Venta,
)
from backend.app.services.caja_service import (
    CajaService, MAX_TRANSACTION_ATTEMPTS, _is_retryable_concurrency,
)
from backend.app.services.exceptions import ConflictError
from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.test_sprint_9_5c_a_solicitud_correccion import (
    db_session, request, seed, snapshot,  # noqa: F401
)
from backend.tests.test_sprint_9_5c_b_resolucion_correccion import resolution


def sql_error(code=1020, *, invalidated=False):
    return OperationalError(
        "SELECT ... FOR UPDATE", {}, DriverOperationalError(code, "simulated concurrency error"),
        connection_invalidated=invalidated,
    )


def prepare(db, action):
    caja_id, sesion_id, arqueo_id = seed(db)
    solicitud_id = None
    if action == "resolver":
        solicitud_id = CajaService(db).solicitar_correccion_arqueo(arqueo_id, request()).solicitud_id
    return caja_id, sesion_id, arqueo_id, solicitud_id


def operate(service, action, ids):
    caja_id, _, arqueo_id, solicitud_id = ids
    if action == "abrir":
        return service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0"))
    if action == "solicitar":
        return service.solicitar_correccion_arqueo(arqueo_id, request())
    return service.resolver_solicitud_correccion(solicitud_id, resolution())


def simulate_driver_policy(service, monkeypatch):
    # Only error classification uses the MariaDB policy. SQLite's locking and
    # connection stay untouched; these tests do not prove server concurrency.
    monkeypatch.setattr(service, "_should_retry_transaction", lambda error: _is_retryable_concurrency(error, "mysql"))


def lock_target(action):
    return "solicitudes_correccion_arqueo" if action == "resolver" else "sesiones_caja"


def is_target_lock(state, action):
    model = SolicitudCorreccionArqueo if action == "resolver" else SesionCaja
    return (
        state.is_select and state.statement._for_update_arg is not None
        and state.statement.column_descriptions[0]["expr"] is model
        and f"FROM {lock_target(action)}" in str(state.statement)
    )


def unchanged_history(db, ids, before):
    db.expire_all()
    assert snapshot(db.get(SesionCaja, ids[1])) == before[0]
    assert snapshot(db.get(ArqueoCaja, ids[2])) == before[1]
    for model in (MovimientoCaja, PagoVenta, Venta):
        assert db.scalars(select(model)).all() == []


@pytest.mark.parametrize("action", ["abrir", "solicitar", "resolver"])
@pytest.mark.parametrize("code", [1020, 1205, 1213])
@pytest.mark.parametrize("stage", ["lock", "after_flush"])
def test_recuperable_revierte_y_repite_en_transaccion_nueva(db_session, monkeypatch, action, code, stage):
    ids = prepare(db_session, action)
    before = snapshot(db_session.get(SesionCaja, ids[1])), snapshot(db_session.get(ArqueoCaja, ids[2]))
    db_session.rollback()
    service = CajaService(db_session)
    simulate_driver_policy(service, monkeypatch)
    transactions, rolled_back = [], []
    event.listen(db_session, "after_begin", lambda db, transaction, connection: transactions.append(transaction))
    original_rollback = db_session.rollback
    def rollback():
        original_rollback()
        assert db_session.get_transaction() is None
        rolled_back.append(True)
    monkeypatch.setattr(db_session, "rollback", rollback)
    commit = Mock(wraps=db_session.commit)
    monkeypatch.setattr(db_session, "commit", commit)
    failures = []
    def fail_once():
        if not failures:
            failures.append(sql_error(code))
            raise failures[0]
    if stage == "lock":
        def before_read(state):
            if is_target_lock(state, action):
                fail_once()
        event.listen(db_session, "do_orm_execute", before_read)
    else:
        event.listen(db_session, "after_flush_postexec", lambda *args: fail_once())
    result = operate(service, action, ids)
    assert len(transactions) == 2 and transactions[0] is not transactions[1]
    assert len(rolled_back) == 1
    assert commit.call_count == 1
    unchanged_history(db_session, ids, before)
    assert len(db_session.scalars(select(SesionCaja)).all()) == (2 if action == "abrir" else 1)
    requests = db_session.scalars(select(SolicitudCorreccionArqueo)).all()
    assert len(requests) == (0 if action == "abrir" else 1)
    if action == "resolver":
        assert requests[0].estado == SolicitudCorreccionArqueoEstado.APROBADA
        assert requests[0].supervisor_id == result.supervisor_id == 900


@pytest.mark.parametrize("action", ["abrir", "solicitar", "resolver"])
def test_agotamiento_preserva_error_tecnico_y_no_confirma(db_session, monkeypatch, action):
    ids = prepare(db_session, action)
    service = CajaService(db_session)
    simulate_driver_policy(service, monkeypatch)
    calls = []
    failure = sql_error()
    def fail_lock(state):
        if is_target_lock(state, action):
            calls.append(db_session.get_transaction())
            raise failure
    event.listen(db_session, "do_orm_execute", fail_lock)
    commit = Mock(wraps=db_session.commit)
    monkeypatch.setattr(db_session, "commit", commit)
    with pytest.raises(OperationalError) as caught:
        operate(service, action, ids)
    assert caught.value is failure
    assert len(calls) == MAX_TRANSACTION_ATTEMPTS == 3
    assert len({id(transaction) for transaction in calls}) == 3
    assert db_session.get_transaction() is None
    commit.assert_not_called()
    event.remove(db_session, "do_orm_execute", fail_lock)
    if action == "resolver":
        row = db_session.get(SolicitudCorreccionArqueo, ids[3])
        assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
        assert row.supervisor_id is row.resuelta_at is row.observacion is None
    elif action == "solicitar":
        assert db_session.scalars(select(SolicitudCorreccionArqueo)).all() == []
    assert len(db_session.scalars(select(SesionCaja)).all()) == 1


@pytest.mark.parametrize("scenario", ["apertura_duplicada", "plazo_vencido", "solicitud_duplicada", "ya_resuelta"])
def test_reintento_relee_ganador_y_solo_entonces_informa_conflicto(db_session, monkeypatch, scenario):
    action = {"apertura_duplicada": "abrir", "plazo_vencido": "solicitar", "solicitud_duplicada": "solicitar", "ya_resuelta": "resolver"}[scenario]
    ids = prepare(db_session, action)
    service = CajaService(db_session)
    simulate_driver_policy(service, monkeypatch)
    reads = []
    def fail_once(state):
        target = is_target_lock(state, action)
        if scenario in {"plazo_vencido", "solicitud_duplicada"}:
            # New rows may be absent from an old server read view too.
            column = SesionCaja.id if scenario == "plazo_vencido" else SolicitudCorreccionArqueo.id
            target = (
                state.is_select and state.statement._for_update_arg is not None
                and state.statement.column_descriptions[0]["expr"] is column
            )
        if target:
            reads.append(True)
            if len(reads) == 1:
                raise sql_error()
    event.listen(db_session, "do_orm_execute", fail_once)
    competed = []
    real_rollback = db_session.rollback
    def rollback_and_compete():
        real_rollback()
        if competed:
            return
        competed.append(True)
        # A distinct session commits the competing result AFTER rollback, to
        # model the new authoritative state seen by the next transaction.
        with Session(db_session.get_bind(), expire_on_commit=False) as other:
            competitor = CajaService(other)
            if scenario in {"apertura_duplicada", "plazo_vencido"}:
                competitor.abrir_sesion(ids[0], cajero_id=102, efectivo_inicial=Decimal("0"))
            elif scenario == "solicitud_duplicada":
                competitor.solicitar_correccion_arqueo(ids[2], request())
            else:
                competitor.resolver_solicitud_correccion(ids[3], resolution(
                    supervisor_id=901, decision="RECHAZADA", observacion="Decision del ganador",
                ))
    monkeypatch.setattr(db_session, "rollback", rollback_and_compete)
    expected = {
        "apertura_duplicada": "sesion abierta", "plazo_vencido": "plazo termino",
        "solicitud_duplicada": "ya tiene una solicitud", "ya_resuelta": "ya fue resuelta",
    }[scenario]
    with pytest.raises(ConflictError, match=expected):
        operate(service, action, ids)
    assert len(reads) == 2
    if scenario == "ya_resuelta":
        row = db_session.get(SolicitudCorreccionArqueo, ids[3])
        assert row.estado == SolicitudCorreccionArqueoEstado.RECHAZADA
        assert row.supervisor_id == 901
        assert row.observacion == "Decision del ganador"
    if scenario == "plazo_vencido":
        assert db_session.scalars(select(SolicitudCorreccionArqueo)).all() == []
        assert db_session.scalar(select(SesionCaja).where(SesionCaja.cajero_id == 102)).estado == SesionCajaEstado.ABIERTA
    if scenario == "solicitud_duplicada":
        assert len(db_session.scalars(select(SolicitudCorreccionArqueo)).all()) == 1


@pytest.mark.parametrize("code", [1044, 1064, 2006, 2013])
@pytest.mark.parametrize("action", ["abrir", "solicitar", "resolver"])
def test_otros_errores_sql_no_se_reintentan_ni_se_traducen(db_session, monkeypatch, code, action):
    ids = prepare(db_session, action)
    service = CajaService(db_session)
    simulate_driver_policy(service, monkeypatch)
    failure = sql_error(code)
    calls = []
    def fail(state):
        if is_target_lock(state, action):
            calls.append(True)
            raise failure
    event.listen(db_session, "do_orm_execute", fail)
    with pytest.raises(OperationalError) as caught:
        operate(service, action, ids)
    assert caught.value is failure
    assert len(calls) == 1
    assert db_session.get_transaction() is None


@pytest.mark.parametrize("action", ["abrir", "solicitar", "resolver"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_error_al_confirmar_no_repite_operacion_aun_con_codigo_1020(db_session, monkeypatch, action, after_commit):
    ids = prepare(db_session, action)
    service = CajaService(db_session)
    simulate_driver_policy(service, monkeypatch)
    real_commit = db_session.commit
    calls = []
    def commit_with_uncertain_outcome():
        calls.append(True)
        if after_commit:
            real_commit()
        raise sql_error()
    monkeypatch.setattr(db_session, "commit", commit_with_uncertain_outcome)
    with pytest.raises(OperationalError):
        operate(service, action, ids)
    assert len(calls) == 1
    assert len(db_session.scalars(select(SesionCaja)).all()) == (2 if after_commit and action == "abrir" else 1)
    rows = db_session.scalars(select(SolicitudCorreccionArqueo)).all()
    if action == "solicitar":
        assert len(rows) == int(after_commit)
    if action == "resolver":
        assert rows[0].estado == ("APROBADA" if after_commit else "PENDIENTE")


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "sqlite", "postgresql"])
@pytest.mark.parametrize("code", [1020, 1205, 1213, 1064, 1044, 2006, 2013])
def test_clasificacion_por_codigo_y_dialecto(dialect, code):
    assert _is_retryable_concurrency(sql_error(code), dialect) == (
        dialect in {"mysql", "mariadb"} and code in {1020, 1205, 1213}
    )


@pytest.mark.parametrize("error", [
    sql_error("1020"), sql_error(1020, invalidated=True),
    IntegrityError("SQL", {}, DriverOperationalError(1020, "simulated")),
    RuntimeError("1020"), OperationalError("SQL", {}, Exception()),
])
def test_no_clasifica_por_texto_ni_reintenta_conexion_invalidada(error):
    assert not _is_retryable_concurrency(error, "mysql")


def test_agotamiento_http_sigue_siendo_error_tecnico_y_no_conflicto(db_session, monkeypatch):
    ids = prepare(db_session, "resolver")
    monkeypatch.setattr(CajaService, "_should_retry_transaction", lambda self, error: _is_retryable_concurrency(error, "mysql"))
    monkeypatch.setattr(settings, "caja_correcciones_resolucion_dev_enabled", True)
    monkeypatch.setattr(settings, "app_env", "test")
    attempts = []
    def fail(state):
        if is_target_lock(state, "resolver"):
            attempts.append(True)
            raise sql_error()
    event.listen(db_session, "do_orm_execute", fail)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/cajas/solicitudes-correccion/{ids[3]}/resolver",
                json=resolution().model_dump(mode="json"),
            )
            assert response.status_code == 500
        assert len(attempts) == 3
        row = db_session.get(SolicitudCorreccionArqueo, ids[3])
        assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
        assert row.supervisor_id is row.resuelta_at is row.observacion is None
    finally:
        event.remove(db_session, "do_orm_execute", fail)
        app.dependency_overrides.pop(get_db, None)
