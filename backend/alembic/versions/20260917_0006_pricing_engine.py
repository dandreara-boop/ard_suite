"""motor comercial de precios

Revision ID: 20260917_0006
Revises: 20260908_0005
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260917_0006"
down_revision: Union[str, Sequence[str], None] = "20260908_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


condicion_precio_tipo = sa.Enum("BASE", "DERIVADA", name="condicion_precio_tipo", native_enum=False, length=20)
tipo_regla_precio = sa.Enum(
    "INCREMENTO_PORCENTUAL",
    "DESCUENTO_PORCENTUAL",
    name="tipo_regla_precio",
    native_enum=False,
    length=40,
)
tipo_redondeo_precio = sa.Enum(
    "SIN_REDONDEO",
    "ENTERO",
    "MULTIPLO",
    name="tipo_redondeo_precio",
    native_enum=False,
    length=20,
)
origen_precio_articulo = sa.Enum("REGLA", "MANUAL", name="origen_precio_articulo", native_enum=False, length=20)
motivo_auditoria_precio = sa.Enum(
    "CREACION",
    "CAMBIO_BASE",
    "MODIFICACION_MANUAL",
    "RECALCULO_REGLA",
    "CAMBIO_REGLA_MASIVO",
    name="motivo_auditoria_precio",
    native_enum=False,
    length=40,
)


def upgrade() -> None:
    op.create_table(
        "condiciones_comerciales_precio",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo", condicion_precio_tipo, nullable=False),
        sa.Column("condicion_base_id", sa.Integer(), nullable=True),
        sa.Column("tipo_regla", tipo_regla_precio, nullable=True),
        sa.Column("porcentaje", sa.Numeric(9, 4), nullable=True),
        sa.Column("tipo_redondeo", tipo_redondeo_precio, server_default="SIN_REDONDEO", nullable=False),
        sa.Column("multiplo_redondeo", sa.Numeric(12, 2), nullable=True),
        sa.Column("activa", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["condicion_base_id"], ["condiciones_comerciales_precio.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_condiciones_comerciales_precio_codigo"), "condiciones_comerciales_precio", ["codigo"], unique=True)
    op.create_index(op.f("ix_condiciones_comerciales_precio_condicion_base_id"), "condiciones_comerciales_precio", ["condicion_base_id"], unique=False)
    op.create_index(op.f("ix_condiciones_comerciales_precio_tipo"), "condiciones_comerciales_precio", ["tipo"], unique=False)

    op.create_table(
        "precios_articulo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("articulo_id", sa.Integer(), nullable=False),
        sa.Column("condicion_comercial_id", sa.Integer(), nullable=False),
        sa.Column("precio", sa.Numeric(12, 2), nullable=False),
        sa.Column("origen", origen_precio_articulo, server_default="REGLA", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["articulo_id"], ["articulos.id"]),
        sa.ForeignKeyConstraint(["condicion_comercial_id"], ["condiciones_comerciales_precio.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("articulo_id", "condicion_comercial_id", name="uq_precio_articulo_condicion"),
    )
    op.create_index(op.f("ix_precios_articulo_articulo_id"), "precios_articulo", ["articulo_id"], unique=False)
    op.create_index(op.f("ix_precios_articulo_condicion_comercial_id"), "precios_articulo", ["condicion_comercial_id"], unique=False)
    op.create_index(op.f("ix_precios_articulo_origen"), "precios_articulo", ["origen"], unique=False)

    op.create_table(
        "auditoria_precios_articulo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("articulo_id", sa.Integer(), nullable=False),
        sa.Column("condicion_comercial_id", sa.Integer(), nullable=False),
        sa.Column("precio_anterior", sa.Numeric(12, 2), nullable=True),
        sa.Column("precio_nuevo", sa.Numeric(12, 2), nullable=False),
        sa.Column("origen_anterior", origen_precio_articulo, nullable=True),
        sa.Column("origen_nuevo", origen_precio_articulo, nullable=False),
        sa.Column("motivo", motivo_auditoria_precio, nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["articulo_id"], ["articulos.id"]),
        sa.ForeignKeyConstraint(["condicion_comercial_id"], ["condiciones_comerciales_precio.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auditoria_precios_articulo_articulo_id"), "auditoria_precios_articulo", ["articulo_id"], unique=False)
    op.create_index(op.f("ix_auditoria_precios_articulo_condicion_comercial_id"), "auditoria_precios_articulo", ["condicion_comercial_id"], unique=False)
    op.create_index(op.f("ix_auditoria_precios_articulo_motivo"), "auditoria_precios_articulo", ["motivo"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_auditoria_precios_articulo_motivo"), table_name="auditoria_precios_articulo")
    op.drop_index(op.f("ix_auditoria_precios_articulo_condicion_comercial_id"), table_name="auditoria_precios_articulo")
    op.drop_index(op.f("ix_auditoria_precios_articulo_articulo_id"), table_name="auditoria_precios_articulo")
    op.drop_table("auditoria_precios_articulo")
    op.drop_index(op.f("ix_precios_articulo_origen"), table_name="precios_articulo")
    op.drop_index(op.f("ix_precios_articulo_condicion_comercial_id"), table_name="precios_articulo")
    op.drop_index(op.f("ix_precios_articulo_articulo_id"), table_name="precios_articulo")
    op.drop_table("precios_articulo")
    op.drop_index(op.f("ix_condiciones_comerciales_precio_tipo"), table_name="condiciones_comerciales_precio")
    op.drop_index(op.f("ix_condiciones_comerciales_precio_condicion_base_id"), table_name="condiciones_comerciales_precio")
    op.drop_index(op.f("ix_condiciones_comerciales_precio_codigo"), table_name="condiciones_comerciales_precio")
    op.drop_table("condiciones_comerciales_precio")
