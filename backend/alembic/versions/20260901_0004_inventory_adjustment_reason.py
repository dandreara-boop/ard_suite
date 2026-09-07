"""motivo explicito en movimientos de inventario

Revision ID: 20260901_0004
Revises: 20260901_0003
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0004"
down_revision: Union[str, Sequence[str], None] = "20260901_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("movimientos_stock", sa.Column("motivo", sa.String(length=180), nullable=True))


def downgrade() -> None:
    op.drop_column("movimientos_stock", "motivo")
