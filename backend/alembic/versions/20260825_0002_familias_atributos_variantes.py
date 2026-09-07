"""familias de atributos y asistente de variantes

Revision ID: 20260825_0002
Revises: 20260825_0001
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0002"
down_revision: Union[str, Sequence[str], None] = "20260825_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "familias_atributos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_familias_atributos_nombre"), "familias_atributos", ["nombre"], unique=True)

    op.create_table(
        "familias_atributos_atributos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("familia_id", sa.Integer(), nullable=False),
        sa.Column("atributo_id", sa.Integer(), nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        sa.Column("obligatorio", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["atributo_id"], ["atributos.id"]),
        sa.ForeignKeyConstraint(["familia_id"], ["familias_atributos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("familia_id", "atributo_id", name="uq_familia_atributo"),
    )

    op.add_column("articulos", sa.Column("familia_atributos_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_articulos_familia_atributos_id",
        "articulos",
        "familias_atributos",
        ["familia_atributos_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_articulos_familia_atributos_id", "articulos", type_="foreignkey")
    op.drop_column("articulos", "familia_atributos_id")
    op.drop_table("familias_atributos_atributos")
    op.drop_index(op.f("ix_familias_atributos_nombre"), table_name="familias_atributos")
    op.drop_table("familias_atributos")
