"""C2: isolated SQLite, fake clock, no external services or MariaDB connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.sesiones import UTCDateTime, digest
from backend.app.core.passwords import generar_hash_password
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import (
    AuditoriaAutenticacion, ConfiguracionSeguridad, CredencialSesion,
    LimiteAutenticacion, SesionCaja, SesionUsuario, Usuario, RolUsuario,
)
from backend.app.services.autenticacion_service import AutenticacionService, ErrorAutenticacion
from backend.app.services.seguridad_service import ContextoSeguridad, PARAMETROS, politica

PASSWORD = "Frase personal de pruebas C2 2026!"
WRONG = "Otra frase de pruebas equivocada!"
C2_TABLES = {"sesiones_usuario", "credenciales_sesion", "configuracion_seguridad",
             "limites_autenticacion", "auditoria_autenticacion"}


@pytest.fixture(scope="module")
def password_hash():
    return generar_hash_password(PASSWORD)


class Harness:
    def __init__(self, factory):
        self.factory = factory
        self.now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def call(self, method, *args, origen="puesto-local", contexto=None, **kwargs):
        with self.factory() as db:
            return getattr(AutenticacionService(
                db, clock=lambda: self.now, origen=origen, contexto=contexto,
            ), method)(*args, **kwargs)

    def login(self, name="cajero.uno", **kwargs):
        return self.call("login", name, PASSWORD, **kwargs)

    def events(self, event=None):
        with self.factory() as db:
            stmt = select(AuditoriaAutenticacion).order_by(AuditoriaAutenticacion.id)
            if event:
                stmt = stmt.where(AuditoriaAutenticacion.evento == event)
            return list(db.scalars(stmt))

    def session(self, locator):
        with self.factory() as db:
            return db.scalar(select(SesionUsuario).where(SesionUsuario.global_id == locator))

    def policy(self, parameter, value, ambito="GENERAL", clave=""):
        with self.factory.begin() as db:
            db.add(ConfiguracionSeguridad(ambito=ambito, clave=clave, parametro=parameter, valor=value))

    def rotate(self, current):
        pending = self.call("preparar", current["token_renovacion"])
        self.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
        return pending


@pytest.fixture
def h(tmp_path, password_hash):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'c2.db'}",
                           connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as db:
        db.add_all([
            Usuario(nombre_usuario="cajero.uno", nombre_visible="Uno", password_hash=password_hash,
                    rol=RolUsuario.CAJERO, activo=True),
            Usuario(nombre_usuario="cajero.dos", nombre_visible="Dos", password_hash=password_hash,
                    rol=RolUsuario.CAJERO, activo=True),
            Usuario(nombre_usuario="inactivo", nombre_visible="Inactivo", password_hash=password_hash,
                    rol=RolUsuario.CAJERO, activo=False),
        ])
    yield Harness(factory)
    engine.dispose()


def denied(h, method, *args, code="CREDENCIALES_INVALIDAS", **kwargs):
    with pytest.raises(ErrorAutenticacion) as error:
        h.call(method, *args, **kwargs)
    assert error.value.code == code


def test_login_normalizacion_utc_sesion_independiente_de_caja(h):
    pair = h.login(" CAJERO.UNO ")
    current = h.call("actual", pair["token_acceso"])
    assert current["estado"] == "ACTIVA"
    assert current["creada_at"].utcoffset() == timedelta(0)
    assert pair["acceso_hasta"] == h.now + timedelta(minutes=5)
    assert pair["renovacion_hasta"] == h.now + timedelta(minutes=30)
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(SesionCaja)) == 0
        cred = db.scalar(select(CredencialSesion))
        assert cred.acceso_hash == digest(pair["token_acceso"])
        assert cred.renovacion_hash == digest(pair["token_renovacion"])


@pytest.mark.parametrize("name,password", [
    ("cajero.uno", WRONG), ("no.existe", PASSWORD), ("inactivo", PASSWORD),
    ("!", PASSWORD), ("cajero.uno", "corta"),
])
def test_fallos_genericos_persisten_auditoria_y_no_crean_sesion(h, name, password):
    denied(h, "login", name, password)
    assert len(h.events("INTENTO_FALLIDO")) == 1
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(SesionUsuario)) == 0


def test_sesiones_simultaneas_y_cierre_independiente(h):
    first, second = h.login(), h.login(origen="otro-origen")
    assert first["sesion_id"] != second["sesion_id"]
    h.call("logout", first["token_renovacion"])
    denied(h, "actual", first["token_acceso"])
    denied(h, "preparar", first["token_renovacion"])
    assert h.call("actual", second["token_acceso"])["estado"] == "ACTIVA"
    assert h.session(first["sesion_id"]).estado == "CERRADA"


def test_revocacion_invalida_solo_sesion_y_no_permite_recuperarla(h):
    first, second = h.login(), h.login()
    h.call("revocar", first["sesion_id"])
    denied(h, "actual", first["token_acceso"])
    h.advance(1801)
    denied(h, "desbloquear", first["sesion_id"], "cajero.uno", PASSWORD)
    assert h.session(second["sesion_id"]).estado == "ACTIVA"
    assert len(h.events("REVOCACION")) == 1


def test_preparacion_no_invalida_actual_y_candidato_no_autoriza(h):
    old = h.login()
    pending = h.call("preparar", old["token_renovacion"])
    assert pending["estado"] == "PENDIENTE" and pending["acceso_hasta"] is None
    assert h.call("actual", old["token_acceso"])["estado"] == "ACTIVA"
    denied(h, "actual", pending["token_acceso"])
    denied(h, "preparar", pending["token_renovacion"])
    denied(h, "confirmar", pending["rotacion_id"], old["token_renovacion"])


def test_confirmacion_rotacion_respuesta_perdida_y_repeticion_idempotente(h):
    old = h.login()
    new = h.call("preparar", old["token_renovacion"])
    first = h.call("confirmar", new["rotacion_id"], new["token_renovacion"])
    h.advance(10)
    repeated = h.call("confirmar", new["rotacion_id"], new["token_renovacion"])
    assert repeated == first
    assert len(h.events("RENOVACION")) == 1
    assert h.session(old["sesion_id"]).generacion == 2
    denied(h, "actual", old["token_acceso"])
    denied(h, "preparar", old["token_renovacion"])
    assert h.call("actual", new["token_acceso"])["estado"] == "ACTIVA"


def test_preparacion_perdida_se_sustituye_sin_extender_credencial_anterior(h):
    old = h.login()
    first = h.call("preparar", old["token_renovacion"])
    for _ in range(4):
        h.advance(20)
        second = h.call("preparar", old["token_renovacion"])
    denied(h, "confirmar", first["rotacion_id"], first["token_renovacion"])
    with h.factory() as db:
        current = db.scalar(select(CredencialSesion).where(CredencialSesion.estado == "ACTIVA"))
        assert current.renovacion_hasta == old["renovacion_hasta"]
        assert current.acceso_hasta == old["acceso_hasta"]
        assert db.scalar(select(func.count()).select_from(CredencialSesion).where(
            CredencialSesion.estado == "PENDIENTE")) == 1
    h.call("confirmar", second["rotacion_id"], second["token_renovacion"])


@pytest.mark.parametrize("seconds", [60, 61, 1800])
def test_candidatura_expirada_no_puede_confirmarse(h, seconds):
    old = h.login()
    pending = h.call("preparar", old["token_renovacion"])
    h.advance(seconds)
    with pytest.raises(ErrorAutenticacion):
        h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
    assert len(h.events("RENOVACION")) == 0


def test_expiracion_origen_no_se_extiende_por_preparaciones(h):
    h.policy("inactividad", 3600)
    h.policy("renovacion", 3600)
    old = h.login()
    h.advance(3590)
    pending = h.call("preparar", old["token_renovacion"])
    h.advance(10)
    denied(h, "confirmar", pending["rotacion_id"], pending["token_renovacion"], code="SESION_BLOQUEADA")
    denied(h, "preparar", old["token_renovacion"])
    assert h.session(old["sesion_id"]).generacion == 1


def test_expiracion_token_no_finaliza_sesion_logica(h):
    pair = h.login()
    h.advance(300)
    denied(h, "actual", pair["token_acceso"])
    assert h.session(pair["sesion_id"]).estado == "ACTIVA"
    pending = h.call("preparar", pair["token_renovacion"])
    h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])


def test_desbloqueo_tras_horas_sin_tokens_vigentes(h):
    pair = h.login()
    h.advance(7200)
    new = h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)
    assert new["sesion_id"] == pair["sesion_id"]
    assert h.call("actual", new["token_acceso"])["estado"] == "ACTIVA"
    assert len(h.events("BLOQUEO_INACTIVIDAD")) == 1
    assert len(h.events("DESBLOQUEO")) == 1
    denied(h, "preparar", pair["token_renovacion"])


@pytest.mark.parametrize("case", ["wrong_owner", "wrong_password", "unknown", "closed", "revoked"])
def test_localizador_no_autoriza_desbloqueo(h, case):
    pair = h.login()
    locator, name, password = pair["sesion_id"], "cajero.uno", PASSWORD
    if case == "wrong_owner":
        name = "cajero.dos"
    elif case == "wrong_password":
        password = WRONG
    elif case == "unknown":
        locator = str(uuid4())
    elif case == "closed":
        h.call("logout", pair["token_renovacion"])
    else:
        h.call("revocar", locator)
    h.advance(1801)
    denied(h, "desbloquear", locator, name, password)
    denied(h, "actual", locator)
    assert len(h.events("DESBLOQUEO")) == 0


def test_consultas_y_rotaciones_no_extienden_inactividad(h):
    h.policy("inactividad", 60)
    pair = h.login()
    for _ in range(2):
        h.advance(20)
        h.call("actual", pair["token_acceso"])
        pair = h.rotate(pair)
    h.advance(20)
    assert h.call("actual", pair["token_acceso"])["estado"] == "BLOQUEADA"
    denied(h, "preparar", pair["token_renovacion"], code="SESION_BLOQUEADA")
    denied(h, "actual", pair["token_acceso"], actividad=True, code="SESION_BLOQUEADA")
    assert len(h.events("BLOQUEO_INACTIVIDAD")) == 1


def test_actividad_real_no_tiene_limite_absoluto_de_jornada(h):
    h.policy("inactividad", 60)
    h.policy("recuperacion", 3600)
    pair = h.login()
    for index in range(81):
        h.advance(50)
        if index % 4 == 3:
            pair = h.rotate(pair)
        h.call("actual", pair["token_acceso"], actividad=True)
    session = h.session(pair["sesion_id"])
    assert session.estado == "ACTIVA"
    assert session.actividad_at - session.creada_at > timedelta(hours=1)


def test_abandono_expira_recuperacion_no_se_reabre(h):
    h.policy("recuperacion", 3600)
    pair = h.login()
    h.advance(3600)
    denied(h, "desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)
    session = h.session(pair["sesion_id"])
    assert session.estado == "REVOCADA" and session.motivo == "ABANDONO"


def test_intentos_por_cuenta_origen_y_recuperacion(h):
    for _ in range(5):
        denied(h, "login", "cajero.uno", WRONG)
    denied(h, "login", "cajero.uno", PASSWORD)
    assert h.login(origen="otro-origen")["estado"] == "ACTIVA"
    assert h.login(name="cajero.dos")["estado"] == "ACTIVA"
    h.advance(900)
    assert h.login()["estado"] == "ACTIVA"
    assert len(h.events("INTENTO_FALLIDO")) == 6


def test_reinicio_tras_30_minutos_sin_fallos(h):
    for _ in range(4):
        denied(h, "login", "cajero.uno", WRONG)
    h.advance(1800)
    denied(h, "login", "cajero.uno", WRONG)
    assert h.login()["estado"] == "ACTIVA"


def test_limite_origen_entre_nombres_y_sin_bloqueo_global(h):
    h.policy("intentos_origen", 10)
    for index in range(10):
        denied(h, "login", f"desconocido.{index}", WRONG)
    denied(h, "login", "cajero.uno", PASSWORD, code="LIMITE_ORIGEN")
    assert h.login(origen="otro-origen")["estado"] == "ACTIVA"


def test_desbloqueo_sujeto_a_limites_persistentes(h):
    pair = h.login()
    h.advance(1801)
    for _ in range(5):
        denied(h, "desbloquear", pair["sesion_id"], "cajero.uno", WRONG)
    denied(h, "desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)
    h.advance(900)
    assert h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)["estado"] == "ACTIVA"


def test_politica_prioridad_por_parametro_y_contexto_confiable(h):
    h.policy("inactividad", 600)
    h.policy("inactividad", 300, "SUCURSAL", "s1")
    h.policy("inactividad", 120, "PUESTO", "p1")
    h.policy("intentos_cuenta", 4, "SUCURSAL", "s1")
    with h.factory() as db:
        assert politica(db, ContextoSeguridad())["inactividad"] == 600
        assert politica(db, ContextoSeguridad("s1"))["inactividad"] == 300
        result = politica(db, ContextoSeguridad("s1", "p1"))
        assert result["inactividad"] == 120 and result["intentos_cuenta"] == 4
    pair = h.login(contexto=ContextoSeguridad("s1", "p1"))
    denied(h, "actual", pair["token_acceso"], contexto=ContextoSeguridad("s1", "p2"))


@pytest.mark.parametrize("parameter,value", [
    (name, value) for name, (_, minimum, maximum) in PARAMETROS.items()
    for value in (minimum - 1, maximum + 1) if value > 0
])
def test_politica_rechaza_limites_inseguros(h, parameter, value):
    h.policy(parameter, value)
    with pytest.raises(ValueError):
        h.login()


def test_politica_rechaza_combinacion_incoherente(h):
    h.policy("recuperacion", 3600)
    h.policy("inactividad", 7200)
    with pytest.raises(ValueError):
        h.login()


def test_cambio_identidad_revoca_tokens_incluso_con_orm_obsoleto(h):
    pair = h.login()
    with h.factory.begin() as db:
        db.execute(update(Usuario).where(Usuario.nombre_usuario == "cajero.uno")
                   .values(activo=False, version=2))
    denied(h, "actual", pair["token_acceso"])
    assert h.session(pair["sesion_id"]).motivo == "IDENTIDAD_CAMBIADA"


def test_auditoria_sin_secretos_ni_localizadores(h, caplog):
    pair = h.login()
    new = h.rotate(pair)
    denied(h, "login", "cajero.uno", WRONG)
    h.call("logout", new["token_renovacion"])
    audit = str([{column.name: getattr(row, column.name) for column in row.__table__.columns}
                 for row in h.events()]) + caplog.text
    for secret in (PASSWORD, WRONG, pair["token_acceso"], pair["token_renovacion"],
                   pair["sesion_id"], pair["rotacion_id"], new["token_acceso"],
                   new["token_renovacion"], digest(new["token_acceso"])):
        assert secret not in audit


def parallel(*operations):
    barrier = Barrier(len(operations))
    def run(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except ErrorAutenticacion:
            return "denied"
    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        return list(executor.map(run, operations))


def test_preparaciones_concurrentes_una_candidata_vigente(h):
    old = h.login()
    candidates = parallel(*[lambda: h.call("preparar", old["token_renovacion"]) for _ in range(2)])
    outcomes = []
    for candidate in candidates:
        try:
            outcomes.append(h.call("confirmar", candidate["rotacion_id"], candidate["token_renovacion"]))
        except ErrorAutenticacion:
            outcomes.append("denied")
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert len(h.events("RENOVACION")) == 1


def test_confirmaciones_concurrentes_idempotentes(h):
    pair = h.login()
    new = h.call("preparar", pair["token_renovacion"])
    results = parallel(*[lambda: h.call("confirmar", new["rotacion_id"], new["token_renovacion"]) for _ in range(2)])
    assert results[0] == results[1] and isinstance(results[0], dict)
    assert len(h.events("RENOVACION")) == 1
    assert h.session(pair["sesion_id"]).generacion == 2


@pytest.mark.parametrize("action", ["preparar", "logout", "revocar"])
def test_confirmacion_concurrente_con_mutaciones(h, action):
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    arg = pair["sesion_id"] if action == "revocar" else pair["token_renovacion"]
    results = parallel(
        lambda: h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"]),
        lambda: h.call(action, arg),
    )
    if action == "revocar":
        assert h.session(pair["sesion_id"]).estado == "REVOCADA"
        denied(h, "confirmar", pending["rotacion_id"], pending["token_renovacion"])
    else:
        assert sum(result != "denied" for result in results) == 1


@pytest.mark.parametrize("action", ["confirmar", "logout", "revocar"])
def test_desbloqueo_concurrente_no_resucita_sesion_finalizada(h, action):
    h.policy("inactividad", 60)
    h.policy("preparacion", 120)
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    h.advance(61)
    operations = {
        "confirmar": lambda: h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"]),
        "logout": lambda: h.call("logout", pair["token_renovacion"]),
        "revocar": lambda: h.call("revocar", pair["sesion_id"]),
    }
    results = parallel(
        lambda: h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD),
        operations[action],
    )
    if action == "revocar":
        assert h.session(pair["sesion_id"]).estado == "REVOCADA"
    elif action == "confirmar":
        assert results[1] == "denied"
        assert h.session(pair["sesion_id"]).estado == "ACTIVA"
    else:
        assert sum(result != "denied" for result in results) == 1


def test_no_reintenta_commit_fallido(h, monkeypatch):
    pair = h.login()
    with h.factory() as db:
        commit = Mock(side_effect=RuntimeError("commit simulado"))
        monkeypatch.setattr(db, "commit", commit)
        auth = AutenticacionService(db, clock=lambda: h.now)
        with pytest.raises(RuntimeError):
            auth.preparar(pair["token_renovacion"])
        commit.assert_called_once()
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(CredencialSesion)) == 1


@pytest.fixture
def client(h, monkeypatch):
    monkeypatch.setattr("backend.app.api.autenticacion.AutenticacionService",
                        lambda db, **kwargs: AutenticacionService(db, clock=lambda: h.now, **kwargs))
    def override():
        with h.factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_contrato_completo_y_no_expone_secretos_en_errores(h, client):
    login = client.post("/api/auth/login", json={"nombre_usuario": "cajero.uno", "password": PASSWORD})
    assert login.status_code == 201 and login.headers["cache-control"] == "no-store"
    pair = login.json()
    headers = {"Authorization": "Bearer " + pair["token_acceso"]}
    assert client.get("/api/auth/sesion", headers=headers).status_code == 200
    assert client.post("/api/auth/actividad", headers=headers).status_code == 200
    pending = client.post("/api/auth/renovar", json={"token_renovacion": pair["token_renovacion"]}).json()
    response = client.post("/api/auth/renovar/confirmar", json={
        "rotacion_id": pending["rotacion_id"], "token_renovacion": pending["token_renovacion"]})
    assert response.status_code == 200
    assert client.post("/api/auth/logout", json={"token_renovacion": pending["token_renovacion"]}).status_code == 204
    assert client.get("/api/auth/sesion", headers=headers).status_code == 401


@pytest.mark.parametrize("payload", [
    {"nombre_usuario": [PASSWORD], "password": PASSWORD},
    {"nombre_usuario": "cajero.uno", "password": [PASSWORD]},
    {"nombre_usuario": "cajero.uno", "password": PASSWORD, "puesto": PASSWORD},
])
def test_api_validaciones_no_reflejan_entradas(client, payload):
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 422
    assert PASSWORD not in response.text and "input" not in response.text


def test_api_sin_token_y_credenciales_genericas(client):
    assert client.get("/api/auth/sesion").status_code == 401
    results = [client.post("/api/auth/login", json={"nombre_usuario": name, "password": WRONG})
               for name in ("cajero.uno", "no.existe", "inactivo")]
    assert all(response.status_code == 401 for response in results)
    assert len({response.text for response in results}) == 1


def load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/20261110_0013_sesiones_autenticacion.py"
    spec = importlib.util.spec_from_file_location("migration_c2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migracion_aditiva_solo_sqlite_aislada_y_paridad_modelos():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    old = [table for table in Base.metadata.sorted_tables if table.name not in C2_TABLES]
    Base.metadata.create_all(engine, tables=old)
    try:
        with engine.begin() as conn:
            before = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table'")).all())
            migration = load_migration()
            migration.op = Operations(MigrationContext.configure(conn))
            assert migration.down_revision == "20261021_0012"
            migration.upgrade()  # Only this newly-created in-memory SQLite database.
            after = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table'")).all())
            assert all(after[name] == ddl for name, ddl in before.items())
            assert set(after) - set(before) == C2_TABLES
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("index", range(5))
def test_downgrade_prevalida_todas_las_tablas_antes_de_ddl(index):
    migration = load_migration()
    operations = Mock()
    operations.get_context.return_value.as_sql = False
    operations.get_bind.return_value.scalar.side_effect = [0] * index + [1]
    migration.op = operations
    with pytest.raises(RuntimeError, match="Downgrade detenido"):
        migration.downgrade()
    operations.drop_table.assert_not_called()


def test_utc_normaliza_offset_y_rechaza_naive():
    datatype = UTCDateTime()
    local = datetime(2026, 11, 10, 10, tzinfo=timezone(timedelta(hours=-3)))
    stored = datatype.process_bind_param(local, None)
    assert stored == datetime(2026, 11, 10, 13)
    assert datatype.process_result_value(stored, None).tzinfo == timezone.utc
    with pytest.raises(ValueError):
        datatype.process_bind_param(datetime(2026, 11, 10), None)


def test_expiracion_padre_antes_de_confirmar_aunque_candidato_no_expiro(h):
    h.policy("inactividad", 7200)
    h.policy("renovacion", 7200)
    pair = h.login()
    h.advance(7190)
    # A real activity earlier in the test would need a fresh access credential.
    # Set the independent activity clock to isolate the refresh-expiry condition.
    with h.factory.begin() as db:
        db.execute(update(SesionUsuario).values(actividad_at=h.now))
    pending = h.call("preparar", pair["token_renovacion"])
    h.advance(10)
    denied(h, "confirmar", pending["rotacion_id"], pending["token_renovacion"])
    denied(h, "preparar", pair["token_renovacion"])
    assert h.session(pair["sesion_id"]).estado == "ACTIVA"


def test_confirmacion_repetida_despues_de_rotacion_posterior_es_rechazada(h):
    pair = h.login()
    first = h.rotate(pair)
    second = h.rotate(first)
    denied(h, "confirmar", first["rotacion_id"], first["token_renovacion"])
    assert h.call("actual", second["token_acceso"])["estado"] == "ACTIVA"
    assert len(h.events("RENOVACION")) == 2


def test_confirmacion_repetida_no_extiende_expiracion(h):
    h.policy("inactividad", 3600)
    h.policy("renovacion", 3600)
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    initial = h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
    h.advance(300)
    assert h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"]) == initial
    denied(h, "actual", pending["token_acceso"])
    h.advance(3300)
    with pytest.raises(ErrorAutenticacion):
        h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])


def test_fallos_concurrentes_no_superan_cuota_por_cuenta(h):
    results = parallel(*[lambda: h.call("login", "cajero.uno", WRONG) for _ in range(6)])
    assert results == ["denied"] * 6
    denied(h, "login", "cajero.uno", PASSWORD)
    with h.factory() as db:
        buckets = list(db.scalars(select(LimiteAutenticacion)))
        pair = next(row for row in buckets if row.ultimo_fallo_at is not None)
        assert pair.intentos == 5
    assert len(h.events("INTENTO_FALLIDO")) == 7


def test_reserva_lenta_no_descontara_la_cuota_de_otra_generacion(h):
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now)
        reservation = auth._admit("cajero.uno")
        h.advance(1801)
        newer = auth._admit("cajero.uno")
        assert newer[1] > reservation[1]
        with auth._tx():
            auth._success(reservation)
    with h.factory() as db:
        assert db.get(LimiteAutenticacion, newer[0]).intentos == 1


def test_exito_no_postergara_reinicio_desde_ultimo_fallo(h):
    denied(h, "login", "cajero.uno", WRONG)
    h.advance(1700)
    h.login()
    h.advance(100)
    with h.factory() as db:
        auth = AutenticacionService(db, clock=lambda: h.now, origen="puesto-local")
        reservation = auth._admit("cajero.uno")
    with h.factory() as db:
        assert db.get(LimiteAutenticacion, reservation[0]).intentos == 1


def test_sin_servicios_externos(h, monkeypatch):
    import socket
    blocker = Mock(side_effect=AssertionError("La autenticacion debe ser local"))
    monkeypatch.setattr(socket, "create_connection", blocker)
    monkeypatch.setattr(socket.socket, "connect", blocker)
    pair = h.login()
    new = h.rotate(pair)
    h.call("actual", new["token_acceso"])
    h.call("logout", new["token_renovacion"])
    blocker.assert_not_called()


def test_sesiones_no_modifican_turno_comercial_existente(h):
    from backend.tests.test_sprint_9_5c_a_solicitud_correccion import seed
    from backend.app.models import Caja
    from backend.app.services.caja_service import CajaService
    from decimal import Decimal
    with h.factory() as db:
        caja_id, _, _ = seed(db)
        CajaService(db).abrir_sesion(caja_id, cajero_id=100, efectivo_inicial=Decimal("5"))
    with h.factory() as db:
        before = db.execute(select(SesionCaja.__table__)).all()
    pair = h.login()
    pair = h.rotate(pair)
    h.advance(1801)
    pair = h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)
    h.call("logout", pair["token_renovacion"])
    with h.factory() as db:
        assert db.execute(select(SesionCaja.__table__)).all() == before
        assert db.scalar(select(Caja.id)) == caja_id


def test_api_desbloqueo_con_tokens_expirados(client, h):
    pair = client.post("/api/auth/login", json={"nombre_usuario": "cajero.uno", "password": PASSWORD}).json()
    h.advance(7200)
    response = client.post("/api/auth/desbloquear", json={
        "sesion_id": pair["sesion_id"], "nombre_usuario": "cajero.uno", "password": PASSWORD})
    assert response.status_code == 200
    new = response.json()
    assert new["sesion_id"] == pair["sesion_id"]
    assert client.get("/api/auth/sesion", headers={"Authorization": "Bearer " + new["token_acceso"]}).status_code == 200


def test_no_acepta_contexto_de_puesto_enviado_por_cliente(client, h):
    response = client.post("/api/auth/login", json={"nombre_usuario": "cajero.uno", "password": PASSWORD},
                           headers={"X-Puesto": "inventado", "X-Forwarded-For": "192.0.2.1"})
    assert response.status_code == 201
    session = h.session(response.json()["sesion_id"])
    assert session.puesto is None and session.sucursal is None
    assert h.events("INICIO")[0].origen_hash == digest("testclient")


def test_modelos_api_no_representan_secretos_ni_localizadores():
    from backend.app.schemas.autenticacion import LoginRequest, DesbloquearRequest
    locator = uuid4()
    request = DesbloquearRequest(sesion_id=locator, nombre_usuario="cajero.uno", password=PASSWORD)
    assert PASSWORD not in repr(request) and str(locator) not in repr(request)
    assert PASSWORD not in str(request.model_dump())
    assert PASSWORD not in LoginRequest(nombre_usuario="cajero.uno", password=PASSWORD).model_dump_json()


def test_mariadb_ddl_compilado_sin_conectar_precision_y_motor():
    from sqlalchemy.dialects.mysql import dialect
    from sqlalchemy.schema import CreateTable
    for name in C2_TABLES:
        ddl = str(CreateTable(Base.metadata.tables[name]).compile(dialect=dialect()))
        assert "ENGINE=InnoDB" in ddl
        if any(isinstance(column.type, UTCDateTime) for column in Base.metadata.tables[name].columns):
            assert "DATETIME(6)" in ddl


def test_identidad_cambiada_durante_argon_no_emite_credenciales(h, monkeypatch):
    original = AutenticacionService._password
    def change_identity(self, *args):
        verified = original(self, *args)
        with h.factory.begin() as db:
            db.execute(update(Usuario).where(Usuario.id == verified.id).values(version=verified.version + 1))
        return verified
    monkeypatch.setattr(AutenticacionService, "_password", change_identity)
    denied(h, "login", "cajero.uno", PASSWORD)
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(CredencialSesion)) == 0


@pytest.mark.parametrize("action", ["logout", "revocar"])
def test_cierre_invalida_candidaturas_y_confirmaciones_ya_activadas(h, action):
    pair = h.login()
    active = h.rotate(pair)
    pending = h.call("preparar", active["token_renovacion"])
    h.call(action, pair["sesion_id"] if action == "revocar" else active["token_renovacion"])
    for credential in (pair, active, pending):
        denied(h, "confirmar", credential["rotacion_id"], credential["token_renovacion"])
        denied(h, "actual", credential["token_acceso"])


def test_politica_no_conocida_falla_cerrado(h):
    h.policy("parametro_inventado", 1)
    with pytest.raises(ValueError):
        h.login()


def test_downgrade_offline_rechazado_sin_ddl():
    migration = load_migration()
    migration.op = Mock()
    migration.op.get_context.return_value.as_sql = True
    with pytest.raises(RuntimeError):
        migration.downgrade()
    migration.op.get_bind.assert_not_called()
    migration.op.drop_table.assert_not_called()


def test_rechazos_de_cuenta_tambien_consumen_limite_de_origen(h):
    h.policy("intentos_origen", 10)
    for _ in range(5):
        denied(h, "login", "cajero.uno", WRONG)
    for _ in range(5):
        denied(h, "login", "cajero.uno", PASSWORD)
    denied(h, "login", "cajero.dos", PASSWORD, code="LIMITE_ORIGEN")
    assert h.login(origen="otro-origen")["estado"] == "ACTIVA"
    assert len(h.events("INTENTO_FALLIDO")) == 11
