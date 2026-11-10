"""Real concurrency tests: explicit ARD_TEST_DB_* opt-in, ard_suite_pytest only.

Never run this module as part of the safe local C2 validation.
"""
import os

import pytest
from sqlalchemy import func, select

from backend.app.core.passwords import generar_hash_password
from backend.app.db.base import Base
from backend.app.models import CredencialSesion, LimiteAutenticacion, RolUsuario, SesionUsuario, Usuario
from backend.app.services.autenticacion_service import ErrorAutenticacion
from backend.tests.mariadb_test_support import MariaDBTestsNotSelected, mariadb_test_sessions
from backend.tests.test_sprint_9_5c_c2_autenticacion import Harness, PASSWORD, parallel


@pytest.fixture
def mariadb_c2():
    try:
        with mariadb_test_sessions(os.environ, Base.metadata) as (factory, engine):
            with factory.begin() as db:
                db.add(Usuario(nombre_usuario="cajero.uno", nombre_visible="Pruebas aisladas",
                               password_hash=generar_hash_password(PASSWORD), rol=RolUsuario.CAJERO))
            yield Harness(factory)
    except MariaDBTestsNotSelected as error:
        pytest.skip(str(error))


def test_mariadb_c2_preparaciones_concurrentes(mariadb_c2):
    h = mariadb_c2
    pair = h.login()
    outcomes = parallel(*[lambda: h.call("preparar", pair["token_renovacion"]) for _ in range(2)])
    assert all(isinstance(outcome, dict) for outcome in outcomes)
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(CredencialSesion).where(
            CredencialSesion.estado == "PENDIENTE")) == 1


def test_mariadb_c2_confirmacion_concurrente_idempotente(mariadb_c2):
    h = mariadb_c2
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    results = parallel(*[lambda: h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
                         for _ in range(2)])
    assert isinstance(results[0], dict) and results[0] == results[1]
    assert len(h.events("RENOVACION")) == 1
    assert h.session(pair["sesion_id"]).generacion == 2
    assert h.call("actual", pending["token_acceso"])["estado"] == "ACTIVA"
    with pytest.raises(ErrorAutenticacion):
        h.call("preparar", pair["token_renovacion"])


@pytest.mark.parametrize("mutation", ["preparar", "logout", "revocar", "desbloquear"])
def test_mariadb_c2_confirmacion_con_mutacion(mariadb_c2, mutation):
    h = mariadb_c2
    if mutation == "desbloquear":
        h.policy("inactividad", 60)
        h.policy("preparacion", 120)
    pair = h.login()
    pending = h.call("preparar", pair["token_renovacion"])
    if mutation == "desbloquear":
        h.advance(61)
    operations = {
        "preparar": lambda: h.call("preparar", pair["token_renovacion"]),
        "logout": lambda: h.call("logout", pair["token_renovacion"]),
        "revocar": lambda: h.call("revocar", pair["sesion_id"]),
        "desbloquear": lambda: h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD),
    }
    outcomes = parallel(
        lambda: h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"]),
        operations[mutation],
    )
    if mutation == "revocar":
        assert h.session(pair["sesion_id"]).estado == "REVOCADA"
        with h.factory() as db:
            assert all(row.estado == "INVALIDADA" for row in db.scalars(select(CredencialSesion)))
        with pytest.raises(ErrorAutenticacion):
            h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
    elif mutation == "desbloquear":
        assert outcomes[0] == "denied" and isinstance(outcomes[1], dict)
        assert h.session(pair["sesion_id"]).estado == "ACTIVA"
    else:
        assert sum(outcome != "denied" for outcome in outcomes) == 1
        session = h.session(pair["sesion_id"])
        if mutation == "logout" and outcomes[1] != "denied":
            assert session.estado == "CERRADA"
            with h.factory() as db:
                assert all(row.estado == "INVALIDADA" for row in db.scalars(select(CredencialSesion)))
            with pytest.raises(ErrorAutenticacion):
                h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
        elif mutation == "preparar" and isinstance(outcomes[1], dict):
            assert session.generacion == 1
            with pytest.raises(ErrorAutenticacion):
                h.call("confirmar", pending["rotacion_id"], pending["token_renovacion"])
        else:
            assert session.generacion == 2


def test_mariadb_c2_inactividad_recuperacion_y_utc(mariadb_c2):
    h = mariadb_c2
    pair = h.login()
    h.advance(7200)
    new = h.call("desbloquear", pair["sesion_id"], "cajero.uno", PASSWORD)
    session = h.session(pair["sesion_id"])
    assert session.actividad_at == h.now
    assert session.actividad_at.utcoffset().total_seconds() == 0
    assert new["sesion_id"] == pair["sesion_id"]


def test_mariadb_c2_cuota_concurrente_persistente(mariadb_c2):
    h = mariadb_c2
    wrong = "Contrasena incorrecta aislada para C2!"
    outcomes = parallel(*[lambda: h.call("login", "cajero.uno", wrong) for _ in range(6)])
    assert outcomes == ["denied"] * 6
    assert len(h.events("INTENTO_FALLIDO")) == 6
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(SesionUsuario)) == 0
        account = db.scalar(select(LimiteAutenticacion).where(LimiteAutenticacion.ultimo_fallo_at.is_not(None)))
        assert account.intentos == 5 and account.bloqueado_hasta is not None
        assert db.scalar(select(Usuario.activo).where(Usuario.nombre_usuario == "cajero.uno")) is True
