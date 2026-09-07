"""motor de inventario

Revision ID: 20260901_0003
Revises: 20260825_0002
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0003"
down_revision: Union[str, Sequence[str], None] = "20260825_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "destinos_inventario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=False),
        sa.Column("nombre", sa.String(length=180), nullable=False),
        sa.Column("tipo", sa.Enum("LOCAL", "WEB", "DEPOSITO", "OTRO", native_enum=False, length=20), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_destinos_inventario_codigo"), "destinos_inventario", ["codigo"], unique=True)

    op.create_table(
        "stock_actual",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variante_id", sa.Integer(), nullable=False),
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("DISPONIBLE", "RESERVADO", "EN_TRANSITO", "NO_DISPONIBLE", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("cantidad", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["destino_id"], ["destinos_inventario.id"]),
        sa.ForeignKeyConstraint(["variante_id"], ["variantes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "variante_id",
            "destino_id",
            "estado",
            name="uq_stock_actual_variante_destino_estado",
        ),
    )
    op.create_index(op.f("ix_stock_actual_destino_id"), "stock_actual", ["destino_id"], unique=False)
    op.create_index(op.f("ix_stock_actual_variante_id"), "stock_actual", ["variante_id"], unique=False)

    op.create_table(
        "movimientos_stock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("variante_id", sa.Integer(), nullable=False),
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("DISPONIBLE", "RESERVADO", "EN_TRANSITO", "NO_DISPONIBLE", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("cantidad", sa.Integer(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "RECEPCION_COMPRA",
                "VENTA",
                "ANULACION_VENTA",
                "CAMBIO_ENTRADA",
                "CAMBIO_SALIDA",
                "TRANSFERENCIA_SALIDA",
                "TRANSFERENCIA_ENTRADA",
                "AJUSTE_POSITIVO",
                "AJUSTE_NEGATIVO",
                "MERMA",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("origen_tipo", sa.String(length=60), nullable=False),
        sa.Column("origen_id", sa.String(length=80), nullable=True),
        sa.Column("referencia", sa.String(length=120), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("saldo_anterior", sa.Integer(), nullable=True),
        sa.Column("saldo_resultante", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["destino_id"], ["destinos_inventario.id"]),
        sa.ForeignKeyConstraint(["variante_id"], ["variantes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_movimientos_stock_destino_id"), "movimientos_stock", ["destino_id"], unique=False)
    op.create_index(op.f("ix_movimientos_stock_fecha"), "movimientos_stock", ["fecha"], unique=False)
    op.create_index(op.f("ix_movimientos_stock_global_id"), "movimientos_stock", ["global_id"], unique=True)
    op.create_index(op.f("ix_movimientos_stock_tipo"), "movimientos_stock", ["tipo"], unique=False)
    op.create_index(op.f("ix_movimientos_stock_variante_id"), "movimientos_stock", ["variante_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_movimientos_stock_variante_id"), table_name="movimientos_stock")
    op.drop_index(op.f("ix_movimientos_stock_tipo"), table_name="movimientos_stock")
    op.drop_index(op.f("ix_movimientos_stock_global_id"), table_name="movimientos_stock")
    op.drop_index(op.f("ix_movimientos_stock_fecha"), table_name="movimientos_stock")
    op.drop_index(op.f("ix_movimientos_stock_destino_id"), table_name="movimientos_stock")
    op.drop_table("movimientos_stock")
    op.drop_index(op.f("ix_stock_actual_variante_id"), table_name="stock_actual")
    op.drop_index(op.f("ix_stock_actual_destino_id"), table_name="stock_actual")
    op.drop_table("stock_actual")
    op.drop_index(op.f("ix_destinos_inventario_codigo"), table_name="destinos_inventario")
    op.drop_table("destinos_inventario")
