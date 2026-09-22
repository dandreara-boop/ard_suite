from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.catalogo import TimestampMixin


class CondicionPrecioTipo(str, Enum):
    BASE = "BASE"
    DERIVADA = "DERIVADA"


class TipoReglaPrecio(str, Enum):
    INCREMENTO_PORCENTUAL = "INCREMENTO_PORCENTUAL"
    DESCUENTO_PORCENTUAL = "DESCUENTO_PORCENTUAL"


class TipoRedondeoPrecio(str, Enum):
    SIN_REDONDEO = "SIN_REDONDEO"
    ENTERO = "ENTERO"
    MULTIPLO = "MULTIPLO"


class OrigenPrecioArticulo(str, Enum):
    REGLA = "REGLA"
    MANUAL = "MANUAL"


class MotivoAuditoriaPrecio(str, Enum):
    CREACION = "CREACION"
    CAMBIO_BASE = "CAMBIO_BASE"
    MODIFICACION_MANUAL = "MODIFICACION_MANUAL"
    RECALCULO_REGLA = "RECALCULO_REGLA"
    CAMBIO_REGLA_MASIVO = "CAMBIO_REGLA_MASIVO"


class CondicionComercialPrecio(TimestampMixin, Base):
    __tablename__ = "condiciones_comerciales_precio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[CondicionPrecioTipo] = mapped_column(
        SAEnum(CondicionPrecioTipo, native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    condicion_base_id: Mapped[int | None] = mapped_column(
        ForeignKey("condiciones_comerciales_precio.id"), nullable=True, index=True
    )
    tipo_regla: Mapped[TipoReglaPrecio | None] = mapped_column(
        SAEnum(TipoReglaPrecio, native_enum=False, length=40),
        nullable=True,
    )
    porcentaje: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    tipo_redondeo: Mapped[TipoRedondeoPrecio] = mapped_column(
        SAEnum(TipoRedondeoPrecio, native_enum=False, length=20),
        nullable=False,
        default=TipoRedondeoPrecio.SIN_REDONDEO,
        server_default=TipoRedondeoPrecio.SIN_REDONDEO.value,
    )
    multiplo_redondeo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    condicion_base: Mapped["CondicionComercialPrecio | None"] = relationship(remote_side=[id])
    precios_articulo: Mapped[list["PrecioArticulo"]] = relationship(back_populates="condicion_comercial")


class PrecioArticulo(TimestampMixin, Base):
    __tablename__ = "precios_articulo"
    __table_args__ = (
        UniqueConstraint("articulo_id", "condicion_comercial_id", name="uq_precio_articulo_condicion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"), nullable=False, index=True)
    condicion_comercial_id: Mapped[int] = mapped_column(
        ForeignKey("condiciones_comerciales_precio.id"), nullable=False, index=True
    )
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    origen: Mapped[OrigenPrecioArticulo] = mapped_column(
        SAEnum(OrigenPrecioArticulo, native_enum=False, length=20),
        nullable=False,
        default=OrigenPrecioArticulo.REGLA,
        server_default=OrigenPrecioArticulo.REGLA.value,
        index=True,
    )

    articulo: Mapped["Articulo"] = relationship()
    condicion_comercial: Mapped[CondicionComercialPrecio] = relationship(back_populates="precios_articulo")


class AuditoriaPrecioArticulo(Base):
    __tablename__ = "auditoria_precios_articulo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"), nullable=False, index=True)
    condicion_comercial_id: Mapped[int] = mapped_column(
        ForeignKey("condiciones_comerciales_precio.id"), nullable=False, index=True
    )
    precio_anterior: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    precio_nuevo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    origen_anterior: Mapped[OrigenPrecioArticulo | None] = mapped_column(
        SAEnum(OrigenPrecioArticulo, native_enum=False, length=20),
        nullable=True,
    )
    origen_nuevo: Mapped[OrigenPrecioArticulo] = mapped_column(
        SAEnum(OrigenPrecioArticulo, native_enum=False, length=20),
        nullable=False,
    )
    motivo: Mapped[MotivoAuditoriaPrecio] = mapped_column(
        SAEnum(MotivoAuditoriaPrecio, native_enum=False, length=40),
        nullable=False,
        index=True,
    )
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    articulo: Mapped["Articulo"] = relationship()
    condicion_comercial: Mapped[CondicionComercialPrecio] = relationship()
