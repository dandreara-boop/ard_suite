from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError

from backend.app.core.config import Settings, settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    ArqueoCaja, ArqueoCajaTipo, MovimientoCaja, PagoVenta, SesionCaja,
    SesionCajaEstado, SolicitudCorreccionArqueo, SolicitudCorreccionArqueoEstado, Venta,
)
from backend.app.schemas.caja import (
    CerrarSesionCajaRequest, ResolverSolicitudCorreccionRequest,
)
from backend.app.services.caja_service import CajaService
from backend.app.services.exceptions import ConflictError, NotFoundError, ValidationError
from backend.tests.test_sprint_9_5c_a_solicitud_correccion import (
    concurrent_sessions, db_session, request, seed, snapshot,  # noqa: F401
)
from backend.tests.test_sprint_9_5c_continuidad_operativa import collect_sale, movement
from backend.tests.test_sprint_9_5a_efectivo import setup_cash_sales


def resolution(**changes):
    return ResolverSolicitudCorreccionRequest(**{
        "supervisor_id": 900, "decision": "APROBADA",
        "observacion": "  Revisado administrativamente  ", **changes,
    })


def pending(db, amount="95.00"):
    caja_id, sesion_id, arqueo_id = seed(db)
    solicitud = CajaService(db).solicitar_correccion_arqueo(
        arqueo_id, request(importe_corregido=Decimal(amount)),
    )
    return caja_id, sesion_id, arqueo_id, solicitud.solicitud_id


@pytest.mark.parametrize("decision,propuesto,reconocido,residual", [
    ("APROBADA", "105.00", "105.00", "5.00"),
    ("APROBADA", "95.00", "95.00", "-5.00"),
    ("APROBADA", "100.00", "100.00", "0.00"),
    ("APROBADA", "0.00", "0.00", "-100.00"),
    ("RECHAZADA", "105.00", "90.00", None),
])
def test_resolucion_importes_auditoria_e_inmutabilidad(
    db_session, decision, propuesto, reconocido, residual,
):
    _, sesion_id, arqueo_id, solicitud_id = pending(db_session, propuesto)
    sesion = db_session.get(SesionCaja, sesion_id)
    arqueo = db_session.get(ArqueoCaja, arqueo_id)
    solicitud = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
    antes = snapshot(sesion), snapshot(arqueo), snapshot(solicitud)
    commits = []
    event.listen(db_session, "after_commit", lambda db: commits.append(True))
    result = CajaService(db_session).resolver_solicitud_correccion(
        solicitud_id, resolution(decision=decision),
    )
    assert len(commits) == 1
    assert result.estado == decision
    assert result.supervisor_id == 900
    assert isinstance(result.resuelta_at, datetime)
    assert result.observacion == "Revisado administrativamente"
    assert result.conteo_original == Decimal("90.00")
    assert result.conteo_corregido_propuesto == Decimal(propuesto)
    assert result.importe_reconocido == Decimal(reconocido)
    assert result.diferencia_original == Decimal("-10.00")
    assert result.diferencia_corregida == (Decimal(residual) if residual is not None else None)
    db_session.expire_all()
    assert (snapshot(sesion), snapshot(arqueo)) == antes[:2]
    despues = snapshot(solicitud)
    assert {key for key in despues if despues[key] != antes[2][key]} == {
        "estado", "supervisor_id", "resuelta_at", "observacion",
    }
    assert solicitud.estado == result.estado
    assert solicitud.supervisor_id == result.supervisor_id
    assert solicitud.resuelta_at == result.resuelta_at
    assert solicitud.observacion == result.observacion
    for model in (MovimientoCaja, Venta, PagoVenta):
        assert db_session.scalars(select(model)).all() == []


@pytest.mark.parametrize("changes", [
    {"supervisor_id": 0}, {"supervisor_id": -1}, {"supervisor_id": True},
    {"supervisor_id": "900"}, {"supervisor_id": 1.5},
    {"supervisor_id": 2147483648}, {"supervisor_id": None},
    {"decision": "PENDIENTE"}, {"decision": "APROBAR"}, {"decision": None}, {"decision": []},
    {"observacion": ""}, {"observacion": " \n\t "}, {"observacion": None},
])
def test_entradas_invalidas_en_schema_y_servicio(db_session, changes):
    _, _, _, solicitud_id = pending(db_session)
    with pytest.raises(SchemaValidationError):
        resolution(**changes)
    bypass = ResolverSolicitudCorreccionRequest.model_construct(**{
        "supervisor_id": 900, "decision": "APROBADA", "observacion": "Revision", **changes,
    })
    with pytest.raises(ValidationError):
        CajaService(db_session).resolver_solicitud_correccion(solicitud_id, bypass)
    row = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
    assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
    assert row.supervisor_id is row.resuelta_at is row.observacion is None


@pytest.mark.parametrize("decision", ["APROBADA", "RECHAZADA"])
def test_no_se_puede_resolver_otra_vez(db_session, decision):
    _, _, _, solicitud_id = pending(db_session)
    service = CajaService(db_session)
    with pytest.raises(NotFoundError):
        service.resolver_solicitud_correccion(999999, resolution())
    service.resolver_solicitud_correccion(solicitud_id, resolution(decision=decision))
    row = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
    antes = snapshot(row)
    for next_decision in ("APROBADA", "RECHAZADA"):
        with pytest.raises(ConflictError, match="ya fue resuelta"):
            service.resolver_solicitud_correccion(
                solicitud_id, resolution(supervisor_id=901, decision=next_decision),
            )
        assert snapshot(row) == antes


@pytest.mark.parametrize("campo,valor", [
    ("efectivo_esperado", None), ("diferencia", None),
    ("diferencia", Decimal("99.00")), ("primer_conteo", Decimal("91.00")),
    ("tipo", ArqueoCajaTipo.CONTROL),
])
@pytest.mark.parametrize("decision", ["APROBADA", "RECHAZADA"])
def test_historial_incompleto_o_inconsistente_no_resuelve(db_session, campo, valor, decision):
    _, _, arqueo_id, solicitud_id = pending(db_session)
    arqueo = db_session.get(ArqueoCaja, arqueo_id)
    setattr(arqueo, campo, valor)
    db_session.commit()
    antes = snapshot(arqueo)
    with pytest.raises(ConflictError, match="historial"):
        CajaService(db_session).resolver_solicitud_correccion(solicitud_id, resolution(decision=decision))
    row = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
    assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
    assert row.supervisor_id is row.resuelta_at is row.observacion is None
    assert snapshot(arqueo) == antes


@pytest.mark.parametrize("decision", ["APROBADA", "RECHAZADA"])
def test_rollback_despues_de_flush_y_reintento(db_session, monkeypatch, decision):
    _, _, arqueo_id, solicitud_id = pending(db_session)
    row = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
    before = snapshot(row), snapshot(db_session.get(ArqueoCaja, arqueo_id))
    def fail_commit():
        assert db_session.scalar(select(SolicitudCorreccionArqueo.estado)) == decision
        raise OperationalError("COMMIT", {}, RuntimeError("fallo simulado"))
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(OperationalError):
            CajaService(db_session).resolver_solicitud_correccion(solicitud_id, resolution(decision=decision))
    db_session.expire_all()
    assert (snapshot(row), snapshot(db_session.get(ArqueoCaja, arqueo_id))) == before
    CajaService(db_session).resolver_solicitud_correccion(solicitud_id, resolution(decision=decision))
    assert row.estado == decision


def test_consultas_bloquean_solo_solicitud_y_actualizan_cuatro_campos(db_session):
    _, _, _, solicitud_id = pending(db_session)
    selects, updates = [], []
    def capture_select(state):
        if state.is_select:
            selects.append(str(state.statement.compile(dialect=mysql.dialect())))
    def capture_update(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)
    event.listen(db_session, "do_orm_execute", capture_select)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture_update)
    try:
        CajaService(db_session).resolver_solicitud_correccion(solicitud_id, resolution())
    finally:
        event.remove(db_session, "do_orm_execute", capture_select)
        event.remove(db_session.get_bind(), "before_cursor_execute", capture_update)
    assert len(selects) == 2
    assert "FROM solicitudes_correccion_arqueo" in selects[0] and "FOR UPDATE" in selects[0]
    assert "FROM arqueos_caja" in selects[1] and "FOR UPDATE" not in selects[1]
    assert all("JOIN" not in sql for sql in selects)
    assert len(updates) == 1
    assert "UPDATE solicitudes_correccion_arqueo SET" in updates[0]
    assignments = updates[0].split(" SET ")[1].split(" WHERE ")[0]
    assert {item.strip().split("=")[0] for item in assignments.split(",")} == {
        "estado", "supervisor_id", "resuelta_at", "observacion",
    }


@pytest.mark.parametrize("decision", ["APROBADA", "RECHAZADA"])
def test_resolver_despues_de_abrir_turno_preserva_continuidad(db_session, decision):
    ctx = setup_cash_sales(db_session)
    service = CajaService(db_session)
    anterior = ctx["sesion"]
    venta, pago = collect_sale(db_session, ctx, anterior)
    cierre = service.cerrar_sesion(
        anterior.id, CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("29990")),
    )
    respuesta = service.solicitar_correccion_arqueo(cierre.arqueo_id, request(importe_corregido=Decimal("29995")))
    nueva = service.abrir_sesion(anterior.caja_id, cajero_id=101, efectivo_inicial=Decimal("100")).sesion
    nueva_orm = db_session.get(SesionCaja, nueva.id)
    arqueo = db_session.get(ArqueoCaja, cierre.arqueo_id)
    historicos = [anterior, arqueo, venta, pago, nueva_orm]
    antes = [snapshot(row) for row in historicos]
    # Operacion con solicitud todavia pendiente.
    collect_sale(db_session, ctx, nueva_orm)
    service.registrar_movimiento(nueva.id, movement(101))
    service.resolver_solicitud_correccion(respuesta.solicitud_id, resolution(decision=decision))
    # Operacion despues de resolver, sin modificar lo cobrado previamente.
    collect_sale(db_session, ctx, nueva_orm)
    service.registrar_movimiento(nueva.id, movement(101))
    db_session.expire_all()
    assert [snapshot(row) for row in historicos] == antes
    assert nueva_orm.estado == SesionCajaEstado.ABIERTA
    assert anterior.estado == SesionCajaEstado.CERRADA
    assert service.listar_movimientos(anterior.id) == []
    assert len(service.listar_movimientos(nueva.id)) == 2
    assert len(db_session.scalars(select(MovimientoCaja)).all()) == 2
    assert len(db_session.scalars(select(Venta)).all()) == 3
    assert len(db_session.scalars(select(PagoVenta)).all()) == 3
    service.cerrar_sesion(
        nueva.id, CerrarSesionCajaRequest(usuario_id=101, efectivo_final_declarado=Decimal("60120")),
    )
    tercera = service.abrir_sesion(anterior.caja_id, cajero_id=102, efectivo_inicial=Decimal("0")).sesion
    collect_sale(db_session, ctx, db_session.get(SesionCaja, tercera.id))


def test_configuracion_declara_endpoint_apagado_por_defecto():
    assert Settings.model_fields["caja_correcciones_resolucion_dev_enabled"].default is False


@pytest.mark.parametrize("enabled,environment", [(False, "development"), (True, "production")])
def test_endpoint_deshabilitado_no_accede_db_y_no_afecta_otros_endpoints(monkeypatch, enabled, environment):
    monkeypatch.setattr(settings, "caja_correcciones_resolucion_dev_enabled", enabled)
    monkeypatch.setattr(settings, "app_env", environment)
    def no_db():
        pytest.fail("El endpoint deshabilitado no debe acceder a la base")
    app.dependency_overrides[get_db] = no_db
    try:
        with TestClient(app) as client:
            response = client.post("/api/cajas/solicitudes-correccion/1/resolver", json=resolution().model_dump(mode="json"))
            assert response.status_code == 404
            assert response.json()["detail"] == "Endpoint de resolucion deshabilitado"
            assert client.get("/").status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("decision", ["APROBADA", "RECHAZADA"])
def test_endpoint_habilitado_expresamente_contrato_y_errores(db_session, monkeypatch, decision):
    _, _, arqueo_id, solicitud_id = pending(db_session)
    monkeypatch.setattr(settings, "caja_correcciones_resolucion_dev_enabled", True)
    monkeypatch.setattr(settings, "app_env", "test")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            url = f"/api/cajas/solicitudes-correccion/{solicitud_id}/resolver"
            payload = resolution(decision=decision).model_dump(mode="json")
            for changes in ({"decision": "PENDIENTE"}, {"observacion": " "}, {"supervisor_id": 0}, {"supervisor_id": True}):
                assert client.post(url, json={**payload, **changes}).status_code == 422
            for field in payload:
                assert client.post(url, json={key: val for key, val in payload.items() if key != field}).status_code == 422
            assert client.post("/api/cajas/solicitudes-correccion/999999/resolver", json=payload).status_code == 404
            # Structural validation of 900 does NOT prove identity or authorization.
            response = client.post(url, json=payload)
            assert response.status_code == 200
            body = response.json()
            assert set(body) == {
                "solicitud_id", "arqueo_id", "estado", "supervisor_id", "resuelta_at", "observacion",
                "conteo_original", "conteo_corregido_propuesto", "importe_reconocido",
                "diferencia_original", "diferencia_corregida",
            }
            assert body["solicitud_id"] == solicitud_id and body["arqueo_id"] == arqueo_id
            assert body["estado"] == decision and body["supervisor_id"] == 900
            assert datetime.fromisoformat(body["resuelta_at"])
            assert body["observacion"] == "Revisado administrativamente"
            assert [body[key] for key in (
                "conteo_original", "conteo_corregido_propuesto", "importe_reconocido",
                "diferencia_original", "diferencia_corregida",
            )] == [
                "90.00", "95.00", "95.00" if decision == "APROBADA" else "90.00",
                "-10.00", "-5.00" if decision == "APROBADA" else None,
            ]
            assert client.post(url, json=payload).status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("primera,segunda", [
    ("APROBADA", "APROBADA"), ("APROBADA", "RECHAZADA"), ("RECHAZADA", "APROBADA"),
])
def test_dos_supervisores_con_snapshot_previo_resuelven_una_sola_vez(concurrent_sessions, primera, segunda):
    factory, engine = concurrent_sessions
    with factory() as db:
        _, _, _, solicitud_id = pending(db)
    locked, release, preloaded = Event(), Event(), Event()
    def first():
        with factory() as db:
            def before_commit(session):
                locked.set()
                assert release.wait(timeout=10)
            event.listen(db, "before_commit", before_commit, once=True)
            return CajaService(db).resolver_solicitud_correccion(solicitud_id, resolution(decision=primera))
    def second():
        with factory() as db:
            row = db.get(SolicitudCorreccionArqueo, solicitud_id)
            assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
            if engine.dialect.name == "sqlite":
                db.rollback()  # BEGIN IMMEDIATE must start the SQLite transaction.
            preloaded.set()
            with pytest.raises(ConflictError, match="ya fue resuelta"):
                CajaService(db).resolver_solicitud_correccion(
                    solicitud_id, resolution(supervisor_id=901, decision=segunda, observacion="Otra revision"),
                )
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(first)
        try:
            assert locked.wait(timeout=10)
            b = executor.submit(second)
            assert preloaded.wait(timeout=10)
        finally:
            release.set()
        result = a.result(timeout=20)
        b.result(timeout=20)
    with factory() as db:
        row = db.get(SolicitudCorreccionArqueo, solicitud_id)
        assert row.estado == primera
        assert row.supervisor_id == 900
        assert row.observacion == result.observacion
        assert row.resuelta_at == result.resuelta_at


def test_endpoint_historial_incompleto_devuelve_conflicto(db_session, monkeypatch):
    _, _, arqueo_id, solicitud_id = pending(db_session)
    db_session.get(ArqueoCaja, arqueo_id).efectivo_esperado = None
    db_session.commit()
    monkeypatch.setattr(settings, "caja_correcciones_resolucion_dev_enabled", True)
    monkeypatch.setattr(settings, "app_env", "test")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/cajas/solicitudes-correccion/{solicitud_id}/resolver",
                json=resolution().model_dump(mode="json"),
            )
            assert response.status_code == 409
        row = db_session.get(SolicitudCorreccionArqueo, solicitud_id)
        assert row.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
        assert row.supervisor_id is row.resuelta_at is row.observacion is None
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("concurrent_sessions", ["mariadb"], indirect=True)
def test_mariadb_resolucion_en_curso_no_bloquea_apertura_movimiento_venta_y_cobro(concurrent_sessions):
    factory, _ = concurrent_sessions
    with factory() as db:
        ctx = setup_cash_sales(db)
        caja_id, sesion_id = ctx["sesion"].caja_id, ctx["sesion"].id
        service = CajaService(db)
        cierre = service.cerrar_sesion(
            sesion_id, CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("0")),
        )
        solicitud = service.solicitar_correccion_arqueo(cierre.arqueo_id, request())
        solicitud_id = solicitud.solicitud_id
    locked, release = Event(), Event()
    def resolve():
        with factory() as db:
            def before_commit(session):
                locked.set()
                assert release.wait(timeout=15)
            event.listen(db, "before_commit", before_commit, once=True)
            return CajaService(db).resolver_solicitud_correccion(solicitud_id, resolution())
    def operate():
        with factory() as db:
            service = CajaService(db)
            nueva = service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0")).sesion
            service.registrar_movimiento(nueva.id, movement(101))
            collect_sale(db, ctx, db.get(SesionCaja, nueva.id))
            return nueva.id
    with ThreadPoolExecutor(max_workers=2) as executor:
        resolving = executor.submit(resolve)
        try:
            assert locked.wait(timeout=10)
            operating = executor.submit(operate)
            # Must finish while the administrative transaction still holds its lock.
            nueva_id = operating.result(timeout=10)
            assert not resolving.done()
        finally:
            release.set()
        resolving.result(timeout=20)
    with factory() as db:
        assert db.get(SesionCaja, nueva_id).estado == SesionCajaEstado.ABIERTA
        assert db.get(SesionCaja, sesion_id).estado == SesionCajaEstado.CERRADA
