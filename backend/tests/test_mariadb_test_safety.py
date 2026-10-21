"""Safety validation uses fake connections only: no MariaDB access or DDL."""
from unittest.mock import Mock

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table

from backend.tests import mariadb_test_support as support


def explicit_test_env(**changes):
    return {
        "ARD_TEST_DB_HOST": "127.0.0.1", "ARD_TEST_DB_PORT": "3307",
        "ARD_TEST_DB_NAME": "ard_suite_test", "ARD_TEST_DB_USER": "test_user",
        "ARD_TEST_DB_PASSWORD": "dummy-unit-test-value", "ARD_TEST_DB_RESET": "1",
        **changes,
    }


@pytest.fixture(autouse=True)
def forbid_real_connections(monkeypatch):
    # A regression must fail instead of reaching a real server.
    monkeypatch.setattr(support, "create_engine", Mock(side_effect=AssertionError("No real engine permitted")))


@pytest.mark.parametrize("database", [
    "ard_suite_dev", "production", "ard_suite_test_backup", "ard_suite_test_95ca_123",
    "", "ARD_SUITE_TEST", " ard_suite_test", "ard_suite_test ",
])
def test_rechaza_cualquier_base_distinta_a_la_seleccion_explicita(database):
    with pytest.raises(support.MariaDBTestSafetyError):
        with support.mariadb_test_sessions(explicit_test_env(ARD_TEST_DB_NAME=database), MetaData()):
            pytest.fail("No debe construir engine ni acceder al servidor")


@pytest.mark.parametrize("key", support.ENV_KEYS)
def test_rechaza_configuracion_incompleta_sin_fallback(key):
    env = explicit_test_env()
    del env[key]
    env.update({
        "DATABASE_URL": "mysql+pymysql://unused@127.0.0.1:3307/ard_suite_dev",
        "DB_NAME": "ard_suite_dev", "DB_PORT": "3307",
    })
    with pytest.raises(support.MariaDBTestSafetyError):
        with support.mariadb_test_sessions(env, MetaData()):
            pytest.fail("No debe utilizar configuracion de desarrollo")


def test_sin_opt_in_no_usa_database_url_ni_variables_de_app():
    with pytest.raises(support.MariaDBTestsNotSelected):
        with support.mariadb_test_sessions({
            "DATABASE_URL": "mysql+pymysql://unused@127.0.0.1:3307/ard_suite_dev",
            "DB_NAME": "ard_suite_dev",
        }, MetaData()):
            pytest.fail("No debe acceder a una base")


@pytest.mark.parametrize("changes", [
    {"ARD_TEST_DB_HOST": "localhost"}, {"ARD_TEST_DB_HOST": "remote.example"},
    {"ARD_TEST_DB_PORT": "3306"}, {"ARD_TEST_DB_PORT": ""},
    {"ARD_TEST_DB_RESET": "0"}, {"ARD_TEST_DB_RESET": "true"}, {"ARD_TEST_DB_USER": ""},
])
def test_rechaza_host_puerto_y_reset_no_autorizados(changes):
    with pytest.raises(support.MariaDBTestSafetyError):
        with support.mariadb_test_sessions(explicit_test_env(**changes), MetaData()):
            pytest.fail("No debe construir engine")


@pytest.mark.parametrize("cross_schema", ["table", "foreign_key", "metadata"])
def test_rechaza_metadata_que_podria_tocar_otro_esquema(cross_schema):
    metadata = MetaData(schema="ard_suite_dev" if cross_schema == "metadata" else None)
    if cross_schema == "table":
        Table("ventas", metadata, Column("id", Integer), schema="ard_suite_dev")
    elif cross_schema == "foreign_key":
        Table("detalle", metadata, Column("venta_id", Integer, ForeignKey("ard_suite_dev.ventas.id")))
    with pytest.raises(support.MariaDBTestSafetyError, match="esquemas explicitos"):
        with support.mariadb_test_sessions(explicit_test_env(), metadata):
            pytest.fail("No debe construir engine")


def test_url_propia_no_depende_de_app_y_no_expone_password():
    env = explicit_test_env(ARD_TEST_DB_PASSWORD="dummy@:/%value")
    config = support.MariaDBTestConfig.from_environ(env)
    url = config.url()
    assert (url.host, url.port, url.database, url.drivername) == (
        "127.0.0.1", 3307, "ard_suite_test", "mysql+pymysql",
    )
    assert url.password == env["ARD_TEST_DB_PASSWORD"]
    assert env["ARD_TEST_DB_PASSWORD"] not in repr(config)
    assert env["ARD_TEST_DB_PASSWORD"] not in str(url)


@pytest.mark.parametrize("identity", [
    ("ard_suite_dev", 3307, "12.2.2-MariaDB"), (None, 3307, "12.2.2-MariaDB"),
    ("other_test", 3307, "12.2.2-MariaDB"), ("ard_suite_test", 3306, "12.2.2-MariaDB"),
    ("ard_suite_test", 3307, "8.0-MySQL"), None,
])
def test_checkout_rechaza_conexion_real_distinta(identity):
    dbapi = Mock()
    dbapi.cursor.return_value.fetchone.return_value = identity
    with pytest.raises(support.MariaDBTestSafetyError):
        support._check_checkout(dbapi, None, None)
    dbapi.cursor.return_value.execute.assert_called_once_with(support.IDENTITY_QUERY)
    dbapi.cursor.return_value.close.assert_called_once()


def fake_server(monkeypatch, *, tables=(), views=(), acquired=1):
    engine, owner, metadata = Mock(), Mock(), Mock(schema=None, tables={})
    engine.connect.return_value.__enter__ = Mock(return_value=owner)
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    identity = ["ard_suite_test", 3307, "12.2.2-MariaDB"]
    statements = []
    def execute(statement):
        statements.append(statement)
        result = Mock()
        if statement == support.IDENTITY_QUERY:
            result.one.return_value = tuple(identity)
        elif "GET_LOCK" in statement:
            result.scalar.return_value = acquired
        return result
    owner.exec_driver_sql.side_effect = execute
    constructor = Mock(return_value=engine)
    monkeypatch.setattr(support, "create_engine", constructor)
    listener = Mock()
    monkeypatch.setattr(support.event, "listen", listener)
    monkeypatch.setattr(support, "inspect", Mock(return_value=Mock(
        get_table_names=Mock(return_value=list(tables)), get_view_names=Mock(return_value=list(views)),
    )))
    return engine, owner, metadata, identity, statements, constructor, listener


@pytest.mark.parametrize("tables,views,acquired", [
    (["ventas"], [], 1), ([], ["vista"], 1), ([], [], 0), ([], [], None),
])
def test_no_borra_esquema_preexistente_ni_de_otra_ejecucion(monkeypatch, tables, views, acquired):
    engine, _, metadata, _, _, _, _ = fake_server(monkeypatch, tables=tables, views=views, acquired=acquired)
    with pytest.raises(support.MariaDBTestSafetyError):
        with support.mariadb_test_sessions(explicit_test_env(), metadata):
            pytest.fail("No debe preparar una base ocupada")
    metadata.create_all.assert_not_called()
    metadata.drop_all.assert_not_called()
    engine.dispose.assert_called_once()


def test_preparacion_y_limpieza_solo_tablas_de_base_validada(monkeypatch):
    engine, owner, metadata, _, statements, constructor, listener = fake_server(monkeypatch)
    with support.mariadb_test_sessions(explicit_test_env(), metadata) as (factory, result_engine):
        assert result_engine is engine
        assert factory.kw["bind"] is engine
        metadata.create_all.assert_called_once_with(owner)
        metadata.drop_all.assert_not_called()
    metadata.drop_all.assert_called_once_with(owner)
    assert all("CREATE DATABASE" not in sql and "DROP DATABASE" not in sql for sql in statements)
    assert any("RELEASE_LOCK" in sql for sql in statements)
    url = constructor.call_args.args[0]
    assert url.database == "ard_suite_test" and url.port == 3307
    assert constructor.call_args.kwargs["isolation_level"] == "REPEATABLE READ"
    listener.assert_called_once_with(engine, "checkout", support._check_checkout)
    engine.dispose.assert_called_once()


def test_teardown_rechaza_base_cambiada_sin_ddl(monkeypatch):
    engine, _, metadata, identity, _, _, _ = fake_server(monkeypatch)
    with pytest.raises(support.MariaDBTestSafetyError):
        with support.mariadb_test_sessions(explicit_test_env(), metadata):
            identity[0] = "ard_suite_dev"
    metadata.create_all.assert_called_once()
    metadata.drop_all.assert_not_called()
    engine.dispose.assert_called_once()


def test_error_de_test_limpia_solo_su_esquema_y_cierra_recursos(monkeypatch):
    engine, owner, metadata, _, _, _, _ = fake_server(monkeypatch)
    with pytest.raises(RuntimeError, match="fallo simulado"):
        with support.mariadb_test_sessions(explicit_test_env(), metadata):
            raise RuntimeError("fallo simulado")
    metadata.drop_all.assert_called_once_with(owner)
    engine.dispose.assert_called_once()
