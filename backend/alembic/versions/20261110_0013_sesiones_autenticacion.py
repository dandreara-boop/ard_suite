"""Autenticacion local y sesiones individuales (Sprint 9.5C-C2).

Revision ID: 20261110_0013
Revises: 20261021_0012

Only additive identity structures. MariaDB DDL may commit partially.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261110_0013"
down_revision = "20261021_0012"
branch_labels = None
depends_on = None


def _utc():
    return sa.DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb")


def upgrade():
    op.create_table(
        "sesiones_usuario",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("global_id", sa.String(36), nullable=False, unique=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("usuario_version", sa.Integer(), nullable=False),
        sa.Column("sucursal", sa.String(64)),
        sa.Column("puesto", sa.String(64)),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("generacion", sa.Integer(), nullable=False),
        sa.Column("creada_at", _utc(), nullable=False),
        sa.Column("actividad_at", _utc(), nullable=False),
        sa.Column("bloqueada_at", _utc()),
        sa.Column("finalizada_at", _utc()),
        sa.Column("motivo", sa.String(32)),
        sa.CheckConstraint("estado IN ('ACTIVA','BLOQUEADA','CERRADA','REVOCADA')", name="ck_sesion_usuario_estado"),
        sa.CheckConstraint("generacion >= 1", name="ck_sesion_usuario_generacion"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_sesion_usuario_usuario_estado", "sesiones_usuario", ["usuario_id", "estado"])
    op.create_table(
        "credenciales_sesion",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rotacion_id", sa.String(36), nullable=False, unique=True),
        sa.Column("sesion_id", sa.Integer(), sa.ForeignKey("sesiones_usuario.id"), nullable=False),
        sa.Column("generacion", sa.Integer(), nullable=False),
        sa.Column("origen_generacion", sa.Integer(), nullable=False),
        sa.Column("acceso_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("renovacion_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("creada_at", _utc(), nullable=False),
        sa.Column("confirmar_hasta", _utc()),
        sa.Column("activada_at", _utc()),
        sa.Column("acceso_hasta", _utc()),
        sa.Column("renovacion_hasta", _utc()),
        sa.Column("invalidada_at", _utc()),
        sa.CheckConstraint("estado IN ('PENDIENTE','ACTIVA','INVALIDADA')", name="ck_credencial_sesion_estado"),
        sa.CheckConstraint("generacion >= 1 AND origen_generacion >= 0", name="ck_credencial_sesion_generacion"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_credencial_sesion_sesion_estado", "credenciales_sesion", ["sesion_id", "estado"])
    op.create_table(
        "configuracion_seguridad",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ambito", sa.String(8), nullable=False),
        sa.Column("clave", sa.String(64), nullable=False),
        sa.Column("parametro", sa.String(40), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("ambito", "clave", "parametro", name="uq_config_seguridad_ambito_clave_parametro"),
        sa.CheckConstraint("ambito IN ('GENERAL','SUCURSAL','PUESTO')", name="ck_config_seguridad_ambito"),
        sa.CheckConstraint("(ambito = 'GENERAL' AND clave = '') OR (ambito <> 'GENERAL' AND clave <> '')", name="ck_config_seguridad_clave"),
        sa.CheckConstraint("valor > 0 AND version >= 1", name="ck_config_seguridad_valor_version"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "limites_autenticacion",
        sa.Column("clave", sa.String(64), primary_key=True),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("generacion", sa.Integer(), nullable=False),
        sa.Column("ultimo_at", _utc(), nullable=False),
        sa.Column("ultimo_fallo_at", _utc()),
        sa.Column("bloqueado_hasta", _utc()),
        sa.CheckConstraint("intentos >= 0 AND generacion >= 1", name="ck_limite_autenticacion_intentos"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "auditoria_autenticacion",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evento", sa.String(32), nullable=False),
        sa.Column("fecha", _utc(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id")),
        sa.Column("sesion_id", sa.Integer(), sa.ForeignKey("sesiones_usuario.id")),
        sa.Column("origen_hash", sa.String(64), nullable=False),
        sa.Column("motivo", sa.String(32)),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_auditoria_autenticacion_fecha", "auditoria_autenticacion", ["fecha"])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Downgrade requiere comprobar que no se pierde historial de autenticacion")
    tables = ("auditoria_autenticacion", "credenciales_sesion", "sesiones_usuario",
              "limites_autenticacion", "configuracion_seguridad")
    bind = op.get_bind()
    # Check every table before the first DDL; never silently discard security history.
    for table in tables:
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table}")):
            raise RuntimeError("Downgrade detenido: existe informacion de autenticacion")
    for table in tables:
        op.drop_table(table)
