"""identificacion y trazabilidad del efectivo

Revision ID: 20261007_0010
Revises: 20261006_0009
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261007_0010"
down_revision: Union[str, Sequence[str], None] = "20261006_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "medios_pago",
        sa.Column("es_efectivo", sa.Boolean(), server_default="0", nullable=False),
    )
    op.execute(
        """
        ALTER TABLE medios_pago
        ADD COLUMN efectivo_unico TINYINT
        GENERATED ALWAYS AS (
            CASE WHEN es_efectivo = 1 THEN 1 ELSE NULL END
        ) VIRTUAL
        """
    )
    op.create_index("uq_medios_pago_unico_efectivo", "medios_pago", ["efectivo_unico"], unique=True)

    op.add_column("pagos_venta", sa.Column("medio_pago_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_pagos_venta_medio_pago_id"), "pagos_venta", ["medio_pago_id"], unique=False)
    op.execute(
        """
        UPDATE pagos_venta pv
        INNER JOIN medios_pago mp ON mp.codigo = pv.medio_pago
        SET pv.medio_pago_id = mp.id
        WHERE pv.medio_pago_id IS NULL
        """
    )
    op.create_foreign_key(
        op.f("fk_pagos_venta_medio_pago_id_medios_pago"),
        "pagos_venta",
        "medios_pago",
        ["medio_pago_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_pagos_venta_medio_pago_id_medios_pago"), "pagos_venta", type_="foreignkey")
    op.drop_index(op.f("ix_pagos_venta_medio_pago_id"), table_name="pagos_venta")
    op.drop_column("pagos_venta", "medio_pago_id")

    op.drop_index("uq_medios_pago_unico_efectivo", table_name="medios_pago")
    op.drop_column("medios_pago", "efectivo_unico")
    op.drop_column("medios_pago", "es_efectivo")
