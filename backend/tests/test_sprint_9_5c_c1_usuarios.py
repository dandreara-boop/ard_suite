from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import getpass
import importlib.util
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock
from uuid import UUID, uuid4
import warnings

from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from backend.app.cli import crear_primer_administrador as cli
from backend.app.core.passwords import generar_hash_password, verificar_password
from backend.app.db.base import Base
from backend.app.models import InicializacionIdentidad, RolUsuario, Usuario, SesionCaja
from backend.app.permissions.usuarios import PERMISOS_POR_ROL, Permiso, tiene_permiso
from backend.app.schemas.usuario import UsuarioCreate, UsuarioRead, normalizar_nombre_usuario
from backend.app.services.exceptions import ConflictError, ValidationError
from backend.app.services.usuario_service import UsuarioService
from backend.tests.test_sprint_9_5c_a_solicitud_correccion import db_session, seed, snapshot  # noqa: F401

TEST_PASSWORD = "Frase individual de pruebas 2026!"


def user_data(**changes):
    return UsuarioCreate(**{
        "nombre_usuario": "cajero.uno", "nombre_visible": "Cajero Uno",
        "password": TEST_PASSWORD, **changes,
    })


def bootstrap(db, **changes):
    return UsuarioService(db).crear_primer_administrador(user_data(rol=RolUsuario.ADMINISTRADOR, **changes))


def test_creacion_identidad_secreta_y_sin_vinculos_historicos(db_session):
    _, sesion_id, _ = seed(db_session)
    old_session = db_session.get(SesionCaja, sesion_id)
    before = snapshot(old_session)
    result = UsuarioService(db_session).crear_usuario(user_data(nombre_usuario="  CAJERO.UNO ", nombre_visible="  Cajero Uno  "))
    assert result.nombre_usuario == "cajero.uno" and result.nombre_visible == "Cajero Uno"
    assert str(UUID(result.global_id)) == result.global_id
    assert result.activo and result.rol == RolUsuario.CAJERO and result.version == 1
    assert result.created_at and result.updated_at
    stored = db_session.get(Usuario, result.id)
    assert stored.password_hash.startswith("$argon2id$") and stored.password_hash != TEST_PASSWORD
    assert verificar_password(TEST_PASSWORD, stored.password_hash)
    assert "password" not in result.model_dump() and "password_hash" not in result.model_dump()
    assert TEST_PASSWORD not in repr(user_data()) and TEST_PASSWORD not in str(user_data().model_dump())
    assert db_session.get(InicializacionIdentidad, 1).inicializada_at is not None
    db_session.expire_all()
    assert snapshot(old_session) == before
    assert not Usuario.__table__.foreign_keys
    # C2 adds identity-only references; historical commercial IDs remain unrelated.
    identity_tables = {"usuarios", "sesiones_usuario", "auditoria_autenticacion"}
    assert not any(fk.target_fullname.startswith("usuarios.") for table in Base.metadata.tables.values() if table.name not in identity_tables for fk in table.foreign_keys)


@pytest.mark.parametrize("raw,expected", [("  CAJERO.UNO ", "cajero.uno"), ("ＡＤＭＩＮ_１", "admin_1"), ("Caja-01", "caja-01")])
def test_normalizacion_compartida_por_schema_y_modelo(raw, expected):
    assert normalizar_nombre_usuario(raw) == expected
    assert user_data(nombre_usuario=raw).nombre_usuario == expected
    assert Usuario(nombre_usuario=raw).nombre_usuario == expected


@pytest.mark.parametrize("name", ["", "ab", "a" * 65, "con espacio", "jose@local", "josé", "../admin", "_admin"])
def test_rechazo_nombres_invalidos(name):
    with pytest.raises(SchemaValidationError):
        user_data(nombre_usuario=name)


def test_unicidad_normalizada_en_servicio_y_en_base(db_session):
    UsuarioService(db_session).crear_usuario(user_data())
    with pytest.raises(ConflictError):
        UsuarioService(db_session).crear_usuario(user_data(nombre_usuario="CAJERO.UNO"))
    db_session.add(Usuario(nombre_usuario=" CAJERO.UNO ", nombre_visible="Duplicado", password_hash="not-a-real-hash", rol=RolUsuario.CAJERO))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert len(db_session.scalars(select(Usuario)).all()) == 1


def test_hash_aleatorio_argon2id_y_verificacion():
    first, second = generar_hash_password(TEST_PASSWORD), generar_hash_password(TEST_PASSWORD)
    assert first != second and first.startswith("$argon2id$")
    assert verificar_password(TEST_PASSWORD, first)
    assert not verificar_password("Otra frase individual equivocada", first)
    assert not verificar_password(TEST_PASSWORD, "hash invalido")
    assert not verificar_password(TEST_PASSWORD, "$argon2id$roto")
    assert not verificar_password(None, first)


@pytest.mark.parametrize("password", ["", "corta", "x" * 14, " " * 20, "x" * 1025])
def test_politica_password_no_expone_valor(password):
    with pytest.raises(ValidationError):
        generar_hash_password(password)
    with pytest.raises(SchemaValidationError) as error:
        user_data(password=password)
    assert "input_value" not in str(error.value)


def test_password_frase_unicode_no_se_normaliza():
    password = "  Frase larga con acentos y espacios áé  "
    encoded = generar_hash_password(password)
    assert verificar_password(password, encoded)
    assert not verificar_password(password.strip(), encoded)


def test_credenciales_invalidas_inactivas_y_validas_sin_sesion(db_session):
    service = UsuarioService(db_session)
    account = service.crear_usuario(user_data())
    assert service.verificar_credenciales(" CAJERO.UNO ", TEST_PASSWORD).id == account.id
    assert service.verificar_credenciales("cajero.uno", "Otra frase equivocada 2026") is None
    assert service.verificar_credenciales("no.existe", TEST_PASSWORD) is None
    assert service.verificar_credenciales("!", TEST_PASSWORD) is None
    row = db_session.get(Usuario, account.id)
    row.activo = False
    db_session.commit()
    assert service.verificar_credenciales("cajero.uno", TEST_PASSWORD) is None
    assert db_session.scalars(select(SesionCaja)).all() == []


def test_verificacion_no_confia_en_identidad_orm_activa_obsoleta(db_session):
    service = UsuarioService(db_session)
    account = service.crear_usuario(user_data())
    cached = db_session.get(Usuario, account.id)
    db_session.execute(
        update(Usuario).where(Usuario.id == account.id).values(activo=False)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    assert cached.activo is True  # expire_on_commit=False intentionally keeps it stale.
    assert service.verificar_credenciales(account.nombre_usuario, TEST_PASSWORD) is None
    assert cached.activo is False


@pytest.mark.parametrize("role,allowed", [
    (RolUsuario.ADMINISTRADOR, {Permiso.USUARIOS_ADMINISTRAR}),
    (RolUsuario.SUPERVISOR, {Permiso.CORRECCIONES_RESOLVER}),
    (RolUsuario.CAJERO, {Permiso.CAJA_OPERAR}),
])
def test_roles_permisos_definidos_en_servidor(db_session, role, allowed):
    if role == RolUsuario.ADMINISTRADOR:
        result = bootstrap(db_session)
    else:
        result = UsuarioService(db_session).crear_usuario(user_data(rol=role))
    user = db_session.get(Usuario, result.id)
    assert {permission for permission in Permiso if tiene_permiso(user, permission)} == allowed
    assert not tiene_permiso({"rol": role, "activo": True}, Permiso.CORRECCIONES_RESOLVER)
    user.activo = False
    assert not any(tiene_permiso(user, permission) for permission in Permiso)
    with pytest.raises(TypeError):
        PERMISOS_POR_ROL[role] = frozenset()


def test_bootstrap_unico_aun_si_cuenta_se_desactiva_cambia_rol_o_se_borra(db_session):
    first = bootstrap(db_session)
    row = db_session.get(Usuario, first.id)
    for change in ("activa", "inactiva", "otro_rol", "eliminada"):
        if change == "inactiva":
            row.activo = False
            db_session.commit()
        elif change == "otro_rol":
            row.rol = RolUsuario.CAJERO
            db_session.commit()
        elif change == "eliminada":
            db_session.delete(row)
            db_session.commit()
        with pytest.raises(ConflictError):
            bootstrap(db_session, nombre_usuario="otro.admin")
    assert db_session.get(InicializacionIdentidad, 1) is not None


def test_bootstrap_rechaza_cualquier_identidad_previa_aun_sin_marcador(db_session):
    # Simulate imported rows which did not use the identity service.
    db_session.add(Usuario(nombre_usuario="anterior", nombre_visible="Anterior", password_hash="not-a-real-hash", rol=RolUsuario.SUPERVISOR))
    db_session.commit()
    with pytest.raises(ConflictError, match="existen identidades"):
        bootstrap(db_session)
    assert len(db_session.scalars(select(Usuario)).all()) == 1
    assert db_session.get(InicializacionIdentidad, 1) is None


def test_servicio_no_permite_admin_adicional_ni_roles_arbitrarios(db_session):
    with pytest.raises(ValidationError):
        UsuarioService(db_session).crear_usuario(user_data(rol=RolUsuario.ADMINISTRADOR))
    with pytest.raises(ValidationError):
        UsuarioService(db_session).crear_primer_administrador(user_data())
    with pytest.raises(SchemaValidationError):
        user_data(rol="CLIENTE_ELIGE_SUPERUSUARIO")
    assert db_session.scalars(select(Usuario)).all() == []


@pytest.mark.parametrize("stage", ["flush", "commit"])
def test_bootstrap_rollback_no_deja_usuario_ni_marcador_y_permite_reintento(db_session, monkeypatch, stage):
    original_flush = db_session.flush
    def fail_flush(*args, **kwargs):
        if any(isinstance(row, Usuario) for row in db_session.new):
            raise OperationalError("INSERT", {}, Exception("error simulado"))
        return original_flush(*args, **kwargs)
    with monkeypatch.context() as patch:
        if stage == "flush":
            patch.setattr(db_session, "flush", fail_flush)
        else:
            patch.setattr(db_session, "commit", Mock(side_effect=OperationalError("COMMIT", {}, Exception("error simulado"))))
        with pytest.raises(OperationalError):
            bootstrap(db_session)
    assert db_session.scalars(select(Usuario)).all() == []
    assert db_session.get(InicializacionIdentidad, 1) is None
    db_session.rollback()
    assert bootstrap(db_session).rol == RolUsuario.ADMINISTRADOR


def test_versionado_optimista_global_id_estable(db_session):
    created = UsuarioService(db_session).crear_usuario(user_data())
    first = db_session.get(Usuario, created.id)
    with Session(db_session.get_bind(), expire_on_commit=False) as other:
        stale = other.get(Usuario, created.id)
        first.rol = RolUsuario.SUPERVISOR
        db_session.commit()
        assert first.version == 2 and first.global_id == created.global_id
        stale.activo = False
        with pytest.raises(StaleDataError):
            other.commit()
        other.rollback()
    with pytest.raises(ValueError, match="inmutable"):
        first.global_id = str(uuid4())


def test_dos_instaladores_sqlite_solo_crean_un_administrador(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'identidad.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    barrier = Barrier(2)
    def install(name):
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                bootstrap(db, nombre_usuario=name)
                return "creado"
            except ConflictError:
                return "rechazado"
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(install, ["admin.uno", "admin.dos"]))
        assert sorted(results) == ["creado", "rechazado"]
        with factory() as db:
            assert len(db.scalars(select(Usuario)).all()) == 1
            assert len(db.scalars(select(InicializacionIdentidad)).all()) == 1
    finally:
        engine.dispose()


def prepare_cli(monkeypatch, db):
    monkeypatch.setattr(cli, "_is_local_administrator", lambda: True)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    inputs = iter(["admin.local", "Administrador Local"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: TEST_PASSWORD)
    @contextmanager
    def isolated_session():
        yield db
    monkeypatch.setattr(cli, "_installation_session", isolated_session)


def test_cli_instalacion_y_segunda_ejecucion_sin_secretos(db_session, monkeypatch, capsys):
    prepare_cli(monkeypatch, db_session)
    assert cli.main([]) == 0
    prepare_cli(monkeypatch, db_session)
    assert cli.main([]) == 1
    output = capsys.readouterr()
    assert TEST_PASSWORD not in output.out + output.err
    assert "$argon2" not in output.out + output.err
    assert "admin.local" not in output.out + output.err
    assert len(db_session.scalars(select(Usuario)).all()) == 1


@pytest.mark.parametrize("admin,interactive", [(False, True), (True, False)])
def test_cli_exige_entorno_administrativo_interactivo(monkeypatch, admin, interactive):
    monkeypatch.setattr(cli, "_is_local_administrator", lambda: admin)
    monkeypatch.setattr(cli, "_is_interactive", lambda: interactive)
    factory = Mock(side_effect=AssertionError("No debe abrir conexion"))
    monkeypatch.setattr(cli, "_installation_session", factory)
    assert cli.main([]) == 1
    factory.assert_not_called()


def test_cli_rechaza_argumentos_sin_imprimir_su_contenido(monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError("No debe abrir conexion"))
    monkeypatch.setattr(cli, "_installation_session", factory)
    assert cli.main(["--password", TEST_PASSWORD]) == 2
    captured = capsys.readouterr()
    assert TEST_PASSWORD not in captured.out + captured.err
    factory.assert_not_called()


@pytest.mark.parametrize("failure", ["mismatch", "warning", "weak", "database"])
def test_cli_errores_no_crean_usuario_ni_revelan_secretos(db_session, monkeypatch, capsys, failure):
    prepare_cli(monkeypatch, db_session)
    if failure == "mismatch":
        values = iter([TEST_PASSWORD, "Otra frase que no coincide"])
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(values))
    elif failure == "warning":
        def warned(prompt):
            warnings.warn("no oculto", getpass.GetPassWarning)
            return TEST_PASSWORD
        monkeypatch.setattr(cli.getpass, "getpass", warned)
    elif failure == "weak":
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "corta")
    else:
        monkeypatch.setattr(cli, "_installation_session", Mock(side_effect=RuntimeError(TEST_PASSWORD)))
    assert cli.main([]) == 1
    output = capsys.readouterr()
    assert TEST_PASSWORD not in output.out + output.err and "$argon2" not in output.out + output.err
    assert db_session.scalars(select(Usuario)).all() == []


def load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/20261021_0012_usuarios_identidad.py"
    spec = importlib.util.spec_from_file_location("migration_identidad", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migracion_aditiva_en_sqlite_aislada_sin_cambiar_tablas_historicas(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    historical = [table for table in Base.metadata.tables.values() if table.name not in {"usuarios", "inicializacion_identidad"}]
    Base.metadata.create_all(engine, tables=historical)
    try:
        with engine.begin() as conn:
            before = conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")).all()
            migration = load_migration()
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            assert migration.down_revision == "20261016_0011"
            migration.upgrade()  # Only this fresh, in-memory SQLite schema.
            after = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")).all())
            assert all(after[name] == sql for name, sql in before)
            assert {"usuarios", "inicializacion_identidad"}.issubset(inspect(conn).get_table_names())
            assert not inspect(conn).get_foreign_keys("usuarios")
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("counts", [(1,), (0, 1)])
def test_downgrade_rechaza_perdida_de_identidad_o_marcador(monkeypatch, counts):
    migration = load_migration()
    operations = Mock()
    operations.get_context.return_value.as_sql = False
    operations.get_bind.return_value.scalar.side_effect = counts
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="Downgrade detenido"):
        migration.downgrade()
    operations.drop_table.assert_not_called()
