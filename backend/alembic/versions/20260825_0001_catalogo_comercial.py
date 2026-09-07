"""catalogo comercial sprint 2

Revision ID: 20260825_0001
Revises:
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "categorias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["parent_id"], ["categorias.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_categorias_nombre"), "categorias", ["nombre"], unique=False)

    op.create_table(
        "marcas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_marcas_nombre"), "marcas", ["nombre"], unique=True)

    op.create_table(
        "atributos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("codigo", sa.String(length=30), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("codigo"),
        sa.UniqueConstraint("nombre"),
    )

    op.create_table(
        "proveedores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=False),
        sa.Column("nombre", sa.String(length=180), nullable=False),
        sa.Column("razon_social", sa.String(length=180), nullable=True),
        sa.Column("cuit", sa.String(length=20), nullable=True),
        sa.Column("telefono", sa.String(length=60), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_proveedores_codigo"), "proveedores", ["codigo"], unique=True)

    op.create_table(
        "articulos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=False),
        sa.Column("nombre", sa.String(length=180), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("categoria_id", sa.Integer(), nullable=False),
        sa.Column("marca_id", sa.Integer(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"]),
        sa.ForeignKeyConstraint(["marca_id"], ["marcas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_articulos_codigo"), "articulos", ["codigo"], unique=True)

    op.create_table(
        "valores_atributo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("atributo_id", sa.Integer(), nullable=False),
        sa.Column("valor", sa.String(length=120), nullable=False),
        sa.Column("codigo", sa.String(length=30), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["atributo_id"], ["atributos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("atributo_id", "valor", name="uq_valor_atributo_valor"),
    )
    op.create_index(op.f("ix_valores_atributo_atributo_id"), "valores_atributo", ["atributo_id"])

    op.create_table(
        "articulos_atributos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("articulo_id", sa.Integer(), nullable=False),
        sa.Column("atributo_id", sa.Integer(), nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        sa.Column("obligatorio", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["articulo_id"], ["articulos.id"]),
        sa.ForeignKeyConstraint(["atributo_id"], ["atributos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("articulo_id", "atributo_id", name="uq_articulo_atributo"),
    )

    op.create_table(
        "variantes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("articulo_id", sa.Integer(), nullable=False),
        sa.Column("codigo_barra", sa.String(length=80), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["articulo_id"], ["articulos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_variantes_articulo_id"), "variantes", ["articulo_id"])
    op.create_index(op.f("ix_variantes_codigo_barra"), "variantes", ["codigo_barra"], unique=True)

    op.create_table(
        "articulos_proveedores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("articulo_id", sa.Integer(), nullable=False),
        sa.Column("proveedor_id", sa.Integer(), nullable=False),
        sa.Column("codigo_proveedor", sa.String(length=80), nullable=True),
        sa.Column("proveedor_preferido", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["articulo_id"], ["articulos.id"]),
        sa.ForeignKeyConstraint(["proveedor_id"], ["proveedores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("articulo_id", "proveedor_id", name="uq_articulo_proveedor"),
    )

    op.create_table(
        "variantes_valores_atributo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variante_id", sa.Integer(), nullable=False),
        sa.Column("valor_atributo_id", sa.Integer(), nullable=False),
        sa.Column("atributo_id", sa.Integer(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["atributo_id"], ["atributos.id"]),
        sa.ForeignKeyConstraint(["valor_atributo_id"], ["valores_atributo.id"]),
        sa.ForeignKeyConstraint(["variante_id"], ["variantes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variante_id", "atributo_id", name="uq_variante_atributo"),
        sa.UniqueConstraint("variante_id", "valor_atributo_id", name="uq_variante_valor_atributo"),
    )


def downgrade() -> None:
    op.drop_table("variantes_valores_atributo")
    op.drop_table("articulos_proveedores")
    op.drop_index(op.f("ix_variantes_codigo_barra"), table_name="variantes")
    op.drop_index(op.f("ix_variantes_articulo_id"), table_name="variantes")
    op.drop_table("variantes")
    op.drop_table("articulos_atributos")
    op.drop_index(op.f("ix_valores_atributo_atributo_id"), table_name="valores_atributo")
    op.drop_table("valores_atributo")
    op.drop_index(op.f("ix_articulos_codigo"), table_name="articulos")
    op.drop_table("articulos")
    op.drop_index(op.f("ix_proveedores_codigo"), table_name="proveedores")
    op.drop_table("proveedores")
    op.drop_table("atributos")
    op.drop_index(op.f("ix_marcas_nombre"), table_name="marcas")
    op.drop_table("marcas")
    op.drop_index(op.f("ix_categorias_nombre"), table_name="categorias")
    op.drop_table("categorias")
