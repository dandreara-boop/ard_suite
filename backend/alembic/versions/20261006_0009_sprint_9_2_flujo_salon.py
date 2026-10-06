"""sprint 9 2 flujo operativo salon

Revision ID: 20261006_0009
Revises: 20261006_0008
Create Date: 2026-10-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261006_0009"
down_revision: Union[str, Sequence[str], None] = "20261006_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "secuencias_numero_corto_venta",
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column("ultimo_numero", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["destino_id"], ["destinos_inventario.id"]),
        sa.PrimaryKeyConstraint("destino_id"),
    )
    op.execute(
        """
        INSERT INTO secuencias_numero_corto_venta (destino_id, ultimo_numero)
        SELECT d.id, COALESCE(MAX(v.numero_corto), 0)
        FROM destinos_inventario d
        LEFT JOIN ventas v ON v.destino_id = d.id
        GROUP BY d.id
        """
    )
    op.create_unique_constraint("uq_ventas_destino_numero_corto", "ventas", ["destino_id", "numero_corto"])


def downgrade() -> None:
    op.drop_constraint("uq_ventas_destino_numero_corto", "ventas", type_="unique")
    op.drop_table("secuencias_numero_corto_venta")
