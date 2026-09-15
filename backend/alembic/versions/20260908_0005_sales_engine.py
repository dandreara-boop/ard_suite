"""motor de venta local

Revision ID: 20260908_0005
Revises: 20260901_0004
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_0005"
down_revision: Union[str, Sequence[str], None] = "20260901_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


venta_estado = sa.Enum(
    "ABIERTA",
    "SUSPENDIDA",
    "EN_PAGO",
    "CERRADA",
    "ANULADA",
    name="venta_estado",
    native_enum=False,
    length=20,
)

evento_pendiente_estado = sa.Enum(
    "PENDIENTE",
    "PROCESANDO",
    "PROCESADO",
    "ERROR",
    name="evento_pendiente_estado",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    op.create_table(
        "ventas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("numero_venta", sa.String(length=40), nullable=False),
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column("estado", venta_estado, server_default="ABIERTA", nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cerrada_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["destino_id"], ["destinos_inventario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ventas_destino_id"), "ventas", ["destino_id"], unique=False)
    op.create_index(op.f("ix_ventas_estado"), "ventas", ["estado"], unique=False)
    op.create_index(op.f("ix_ventas_global_id"), "ventas", ["global_id"], unique=True)
    op.create_index(op.f("ix_ventas_numero_venta"), "ventas", ["numero_venta"], unique=True)

    op.create_table(
        "detalles_venta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venta_id", sa.Integer(), nullable=False),
        sa.Column("variante_id", sa.Integer(), nullable=False),
        sa.Column("codigo_articulo", sa.String(length=60), nullable=False),
        sa.Column("codigo_barra", sa.String(length=80), nullable=False),
        sa.Column("descripcion", sa.String(length=240), nullable=False),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(12, 2), nullable=False),
        sa.Column("importe", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["venta_id"], ["ventas.id"]),
        sa.ForeignKeyConstraint(["variante_id"], ["variantes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_detalles_venta_venta_id"), "detalles_venta", ["venta_id"], unique=False)
    op.create_index(op.f("ix_detalles_venta_variante_id"), "detalles_venta", ["variante_id"], unique=False)

    op.create_table(
        "pagos_venta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venta_id", sa.Integer(), nullable=False),
        sa.Column("medio_pago", sa.String(length=60), nullable=False),
        sa.Column("importe", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["venta_id"], ["ventas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pagos_venta_venta_id"), "pagos_venta", ["venta_id"], unique=False)

    op.create_table(
        "eventos_pendientes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("tipo", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("estado", evento_pendiente_estado, server_default="PENDIENTE", nullable=False),
        sa.Column("intentos", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tipo", "aggregate_type", "aggregate_id", name="uq_evento_pendiente_aggregate"),
    )
    op.create_index(op.f("ix_eventos_pendientes_aggregate_id"), "eventos_pendientes", ["aggregate_id"], unique=False)
    op.create_index(op.f("ix_eventos_pendientes_estado"), "eventos_pendientes", ["estado"], unique=False)
    op.create_index(op.f("ix_eventos_pendientes_global_id"), "eventos_pendientes", ["global_id"], unique=True)
    op.create_index(op.f("ix_eventos_pendientes_tipo"), "eventos_pendientes", ["tipo"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_eventos_pendientes_tipo"), table_name="eventos_pendientes")
    op.drop_index(op.f("ix_eventos_pendientes_global_id"), table_name="eventos_pendientes")
    op.drop_index(op.f("ix_eventos_pendientes_estado"), table_name="eventos_pendientes")
    op.drop_index(op.f("ix_eventos_pendientes_aggregate_id"), table_name="eventos_pendientes")
    op.drop_table("eventos_pendientes")
    op.drop_index(op.f("ix_pagos_venta_venta_id"), table_name="pagos_venta")
    op.drop_table("pagos_venta")
    op.drop_index(op.f("ix_detalles_venta_variante_id"), table_name="detalles_venta")
    op.drop_index(op.f("ix_detalles_venta_venta_id"), table_name="detalles_venta")
    op.drop_table("detalles_venta")
    op.drop_index(op.f("ix_ventas_numero_venta"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_global_id"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_estado"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_destino_id"), table_name="ventas")
    op.drop_table("ventas")
