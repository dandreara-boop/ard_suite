"""motor de resolucion comercial y cobro pos

Revision ID: 20260924_0007
Revises: 20260917_0006
Create Date: 2026-09-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260924_0007"
down_revision: Union[str, Sequence[str], None] = "20260917_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "medios_pago",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("condicion_comercial_id", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["condicion_comercial_id"], ["condiciones_comerciales_precio.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_medios_pago_codigo"), "medios_pago", ["codigo"], unique=True)
    op.create_index(op.f("ix_medios_pago_condicion_comercial_id"), "medios_pago", ["condicion_comercial_id"], unique=False)

    op.create_table(
        "resoluciones_comerciales_venta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venta_id", sa.Integer(), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("incremento_redondeo", sa.Numeric(8, 2), nullable=False),
        sa.Column("solicitud", sa.JSON(), nullable=False),
        sa.Column("resultado", sa.JSON(), nullable=False),
        sa.Column("traza", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["venta_id"], ["ventas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venta_id", name="uq_resolucion_comercial_venta"),
    )
    op.create_index(op.f("ix_resoluciones_comerciales_venta_venta_id"), "resoluciones_comerciales_venta", ["venta_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_resoluciones_comerciales_venta_venta_id"), table_name="resoluciones_comerciales_venta")
    op.drop_table("resoluciones_comerciales_venta")
    op.drop_index(op.f("ix_medios_pago_condicion_comercial_id"), table_name="medios_pago")
    op.drop_index(op.f("ix_medios_pago_codigo"), table_name="medios_pago")
    op.drop_table("medios_pago")
