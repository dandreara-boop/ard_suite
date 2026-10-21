"""Opt-in MariaDB tests. Never derive their target from application settings."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import URL, MetaData, create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

TEST_DATABASE = "ard_suite_test"
IDENTITY_QUERY = "SELECT DATABASE(), @@port, VERSION()"
SCHEMA_LOCK = "ard_suite_test_pytest_schema"
ENV_KEYS = (
    "ARD_TEST_DB_HOST", "ARD_TEST_DB_PORT", "ARD_TEST_DB_NAME",
    "ARD_TEST_DB_USER", "ARD_TEST_DB_PASSWORD", "ARD_TEST_DB_RESET",
)


class MariaDBTestSafetyError(RuntimeError):
    pass


class MariaDBTestsNotSelected(MariaDBTestSafetyError):
    pass


@dataclass(frozen=True)
class MariaDBTestConfig:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> MariaDBTestConfig:
        if not any(key in environ for key in ENV_KEYS):
            raise MariaDBTestsNotSelected("MariaDB no seleccionada: configurar ARD_TEST_DB_* explicitamente")
        if environ.get("ARD_TEST_DB_NAME") != TEST_DATABASE:
            raise MariaDBTestSafetyError("ARD_TEST_DB_NAME debe seleccionar exclusivamente ard_suite_test")
        if environ.get("ARD_TEST_DB_HOST") != "127.0.0.1" or environ.get("ARD_TEST_DB_PORT") != "3307":
            raise MariaDBTestSafetyError("Los tests requieren seleccion explicita de 127.0.0.1:3307")
        if not environ.get("ARD_TEST_DB_USER") or "ARD_TEST_DB_PASSWORD" not in environ:
            raise MariaDBTestSafetyError("Se requieren ARD_TEST_DB_USER y ARD_TEST_DB_PASSWORD explicitos")
        if environ.get("ARD_TEST_DB_RESET") != "1":
            raise MariaDBTestSafetyError("Se requiere ARD_TEST_DB_RESET=1 para crear y limpiar tablas de pruebas")
        return cls(
            host="127.0.0.1", port=3307, database=TEST_DATABASE,
            username=environ["ARD_TEST_DB_USER"], password=environ["ARD_TEST_DB_PASSWORD"],
        )

    def url(self) -> URL:
        return URL.create(
            "mysql+pymysql", username=self.username, password=self.password,
            host=self.host, port=self.port, database=self.database,
        )


def _validate_server_identity(row) -> None:
    if row is None or len(row) != 3 or row[0] != TEST_DATABASE or row[1] != 3307:
        raise MariaDBTestSafetyError("Conexion real rechazada: base o puerto no son los de pruebas")
    if not isinstance(row[2], str) or "MariaDB" not in row[2]:
        raise MariaDBTestSafetyError("Conexion real rechazada: se requiere servidor MariaDB")


def _check_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    # Applies to every connection used by the workers, not just schema setup.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(IDENTITY_QUERY)
        _validate_server_identity(cursor.fetchone())
    finally:
        cursor.close()


def _check_connection(connection: Connection) -> None:
    _validate_server_identity(connection.exec_driver_sql(IDENTITY_QUERY).one())


@contextmanager
def mariadb_test_sessions(
    environ: Mapping[str, str], metadata: MetaData,
) -> Iterator[tuple[sessionmaker, Engine]]:
    config = MariaDBTestConfig.from_environ(environ)  # Before constructing any engine.
    if metadata.schema is not None or any(
        table.schema is not None or any(len(fk.target_fullname.split(".")) != 2 for fk in table.foreign_keys)
        for table in metadata.tables.values()
    ):
        raise MariaDBTestSafetyError("Metadata con referencias a esquemas explicitos rechazada")
    # Validate again at the boundary; never accept an alternative URL/engine.
    if config.database != TEST_DATABASE or config.host != "127.0.0.1" or config.port != 3307:
        raise MariaDBTestSafetyError("Configuracion de pruebas rechazada")
    engine = create_engine(
        config.url(), poolclass=NullPool, isolation_level="REPEATABLE READ",
        connect_args={"connect_timeout": 3}, hide_parameters=True, echo=False,
    )
    event.listen(engine, "checkout", _check_checkout)
    try:
        # Keep this connection open: GET_LOCK belongs to the connection and
        # prevents two pytest processes from resetting the same shared schema.
        with engine.connect() as owner:
            _check_connection(owner)
            acquired = owner.exec_driver_sql(f"SELECT GET_LOCK('{SCHEMA_LOCK}', 0)").scalar()
            if acquired != 1:
                raise MariaDBTestSafetyError("ard_suite_test esta ocupada por otra ejecucion de pruebas")
            owns_schema = False
            try:
                inspector = inspect(owner)
                if inspector.get_table_names() or inspector.get_view_names():
                    raise MariaDBTestSafetyError(
                        "ard_suite_test debe estar vacia; no se borran tablas o vistas preexistentes"
                    )
                _check_connection(owner)
                owns_schema = True  # Also clean up a partially failed create_all.
                metadata.create_all(owner)
                owner.commit()
                yield sessionmaker(bind=engine, expire_on_commit=False), engine
            finally:
                try:
                    if owns_schema:
                        owner.rollback()
                        _check_connection(owner)  # Fail closed even during teardown.
                        metadata.drop_all(owner)
                        owner.commit()
                finally:
                    owner.exec_driver_sql(f"SELECT RELEASE_LOCK('{SCHEMA_LOCK}')")
    finally:
        engine.dispose()
