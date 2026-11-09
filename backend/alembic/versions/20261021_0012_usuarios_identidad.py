"""Usuarios individuales e inicializacion local (Sprint 9.5C-C1).

Revision ID: 20261021_0012
Revises: 20261016_0011
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261021_0012"
down_revision: Union[str, Sequence[str], None] = "20261016_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("global_id", sa.String(36), nullable=False),
        sa.Column("nombre_usuario", sa.String(64), nullable=False),
        sa.Column("nombre_visible", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("rol", sa.Enum("ADMINISTRADOR", "SUPERVISOR", "CAJERO", native_enum=False, length=20, name="rol_usuario", create_constraint=True, validate_strings=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_usuarios_version_positiva"),
    )
    op.create_index("ix_usuarios_global_id", "usuarios", ["global_id"], unique=True)
    op.create_index("ix_usuarios_nombre_usuario", "usuarios", ["nombre_usuario"], unique=True)
    op.create_table(
        "inicializacion_identidad",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("inicializada_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_inicializacion_identidad_unica"),
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("El downgrade requiere comprobar que no se pierde identidad")
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM usuarios")) or bind.scalar(sa.text("SELECT COUNT(*) FROM inicializacion_identidad")):
        raise RuntimeError("Downgrade detenido: existen identidades o historial de inicializacion")
    op.drop_table("inicializacion_identidad")
    op.drop_index("ix_usuarios_nombre_usuario", table_name="usuarios")
    op.drop_index("ix_usuarios_global_id", table_name="usuarios")
    op.drop_table("usuarios")
