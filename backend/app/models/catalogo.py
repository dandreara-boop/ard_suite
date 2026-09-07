from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Categoria(TimestampMixin, Base):
    __tablename__ = "categorias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    parent: Mapped["Categoria | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Categoria"]] = relationship(back_populates="parent")
    articulos: Mapped[list["Articulo"]] = relationship(back_populates="categoria")


class Marca(TimestampMixin, Base):
    __tablename__ = "marcas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    articulos: Mapped[list["Articulo"]] = relationship(back_populates="marca")


class Articulo(TimestampMixin, Base):
    __tablename__ = "articulos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(180), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), nullable=False)
    marca_id: Mapped[int | None] = mapped_column(ForeignKey("marcas.id"), nullable=True)
    familia_atributos_id: Mapped[int | None] = mapped_column(
        ForeignKey("familias_atributos.id"), nullable=True
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    categoria: Mapped[Categoria] = relationship(back_populates="articulos")
    marca: Mapped[Marca | None] = relationship(back_populates="articulos")
    familia_atributos: Mapped["FamiliaAtributos | None"] = relationship(back_populates="articulos")
    atributos: Mapped[list["ArticuloAtributo"]] = relationship(
        back_populates="articulo", cascade="all, delete-orphan"
    )
    variantes: Mapped[list["Variante"]] = relationship(back_populates="articulo")
    proveedores: Mapped[list["ArticuloProveedor"]] = relationship(
        back_populates="articulo", cascade="all, delete-orphan"
    )


class Atributo(TimestampMixin, Base):
    __tablename__ = "atributos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    codigo: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    valores: Mapped[list["ValorAtributo"]] = relationship(
        back_populates="atributo", cascade="all, delete-orphan"
    )
    articulos: Mapped[list["ArticuloAtributo"]] = relationship(back_populates="atributo")
    familias: Mapped[list["FamiliaAtributo"]] = relationship(back_populates="atributo")


class FamiliaAtributos(TimestampMixin, Base):
    __tablename__ = "familias_atributos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    atributos: Mapped[list["FamiliaAtributo"]] = relationship(
        back_populates="familia", cascade="all, delete-orphan"
    )
    articulos: Mapped[list[Articulo]] = relationship(back_populates="familia_atributos")


class FamiliaAtributo(TimestampMixin, Base):
    __tablename__ = "familias_atributos_atributos"
    __table_args__ = (UniqueConstraint("familia_id", "atributo_id", name="uq_familia_atributo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    familia_id: Mapped[int] = mapped_column(ForeignKey("familias_atributos.id"), nullable=False)
    atributo_id: Mapped[int] = mapped_column(ForeignKey("atributos.id"), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    obligatorio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    familia: Mapped[FamiliaAtributos] = relationship(back_populates="atributos")
    atributo: Mapped[Atributo] = relationship(back_populates="familias")


class ValorAtributo(TimestampMixin, Base):
    __tablename__ = "valores_atributo"
    __table_args__ = (UniqueConstraint("atributo_id", "valor", name="uq_valor_atributo_valor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    atributo_id: Mapped[int] = mapped_column(ForeignKey("atributos.id"), nullable=False, index=True)
    valor: Mapped[str] = mapped_column(String(120), nullable=False)
    codigo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    atributo: Mapped[Atributo] = relationship(back_populates="valores")
    variantes: Mapped[list["VarianteValorAtributo"]] = relationship(back_populates="valor_atributo")


class ArticuloAtributo(TimestampMixin, Base):
    __tablename__ = "articulos_atributos"
    __table_args__ = (UniqueConstraint("articulo_id", "atributo_id", name="uq_articulo_atributo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"), nullable=False)
    atributo_id: Mapped[int] = mapped_column(ForeignKey("atributos.id"), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    obligatorio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    articulo: Mapped[Articulo] = relationship(back_populates="atributos")
    atributo: Mapped[Atributo] = relationship(back_populates="articulos")


class Variante(TimestampMixin, Base):
    __tablename__ = "variantes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"), nullable=False, index=True)
    codigo_barra: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    articulo: Mapped[Articulo] = relationship(back_populates="variantes")
    valores: Mapped[list["VarianteValorAtributo"]] = relationship(
        back_populates="variante", cascade="all, delete-orphan"
    )


class VarianteValorAtributo(TimestampMixin, Base):
    __tablename__ = "variantes_valores_atributo"
    __table_args__ = (
        UniqueConstraint("variante_id", "valor_atributo_id", name="uq_variante_valor_atributo"),
        UniqueConstraint("variante_id", "atributo_id", name="uq_variante_atributo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variante_id: Mapped[int] = mapped_column(ForeignKey("variantes.id"), nullable=False)
    valor_atributo_id: Mapped[int] = mapped_column(ForeignKey("valores_atributo.id"), nullable=False)
    atributo_id: Mapped[int] = mapped_column(ForeignKey("atributos.id"), nullable=False)

    variante: Mapped[Variante] = relationship(back_populates="valores")
    valor_atributo: Mapped[ValorAtributo] = relationship(back_populates="variantes")
    atributo: Mapped[Atributo] = relationship()


class Proveedor(TimestampMixin, Base):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(180), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(String(180), nullable=True)
    cuit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(60), nullable=True)
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    articulos: Mapped[list["ArticuloProveedor"]] = relationship(
        back_populates="proveedor", cascade="all, delete-orphan"
    )


class ArticuloProveedor(TimestampMixin, Base):
    __tablename__ = "articulos_proveedores"
    __table_args__ = (UniqueConstraint("articulo_id", "proveedor_id", name="uq_articulo_proveedor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"), nullable=False)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), nullable=False)
    codigo_proveedor: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proveedor_preferido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    articulo: Mapped[Articulo] = relationship(back_populates="proveedores")
    proveedor: Mapped[Proveedor] = relationship(back_populates="articulos")
