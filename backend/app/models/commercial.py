from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.catalogo import TimestampMixin


class MedioPago(TimestampMixin, Base):
    __tablename__ = "medios_pago"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    condicion_comercial_id: Mapped[int] = mapped_column(
        ForeignKey("condiciones_comerciales_precio.id"), nullable=False, index=True
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    condicion_comercial: Mapped["CondicionComercialPrecio"] = relationship()


class ResolucionComercialVenta(TimestampMixin, Base):
    __tablename__ = "resoluciones_comerciales_venta"
    __table_args__ = (UniqueConstraint("venta_id", name="uq_resolucion_comercial_venta"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venta_id: Mapped[int] = mapped_column(ForeignKey("ventas.id"), nullable=False, index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    incremento_redondeo: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    solicitud: Mapped[dict] = mapped_column(JSON, nullable=False)
    resultado: Mapped[dict] = mapped_column(JSON, nullable=False)
    traza: Mapped[dict] = mapped_column(JSON, nullable=False)

    venta: Mapped["Venta"] = relationship()
