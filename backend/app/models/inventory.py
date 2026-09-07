from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.catalogo import TimestampMixin


class DestinoInventarioTipo(str, Enum):
    LOCAL = "LOCAL"
    WEB = "WEB"
    DEPOSITO = "DEPOSITO"
    OTRO = "OTRO"


class StockEstado(str, Enum):
    DISPONIBLE = "DISPONIBLE"
    RESERVADO = "RESERVADO"
    EN_TRANSITO = "EN_TRANSITO"
    NO_DISPONIBLE = "NO_DISPONIBLE"


class MovimientoStockTipo(str, Enum):
    RECEPCION_COMPRA = "RECEPCION_COMPRA"
    VENTA = "VENTA"
    ANULACION_VENTA = "ANULACION_VENTA"
    CAMBIO_ENTRADA = "CAMBIO_ENTRADA"
    CAMBIO_SALIDA = "CAMBIO_SALIDA"
    TRANSFERENCIA_SALIDA = "TRANSFERENCIA_SALIDA"
    TRANSFERENCIA_ENTRADA = "TRANSFERENCIA_ENTRADA"
    AJUSTE_POSITIVO = "AJUSTE_POSITIVO"
    AJUSTE_NEGATIVO = "AJUSTE_NEGATIVO"
    MERMA = "MERMA"


class DestinoInventario(TimestampMixin, Base):
    __tablename__ = "destinos_inventario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(180), nullable=False)
    tipo: Mapped[DestinoInventarioTipo] = mapped_column(
        SAEnum(DestinoInventarioTipo, native_enum=False, length=20),
        nullable=False,
    )
    activo: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")

    stocks: Mapped[list["StockActual"]] = relationship(back_populates="destino")
    movimientos: Mapped[list["MovimientoStock"]] = relationship(back_populates="destino")


class StockActual(Base):
    __tablename__ = "stock_actual"
    __table_args__ = (
        UniqueConstraint("variante_id", "destino_id", "estado", name="uq_stock_actual_variante_destino_estado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variante_id: Mapped[int] = mapped_column(ForeignKey("variantes.id"), nullable=False, index=True)
    destino_id: Mapped[int] = mapped_column(
        ForeignKey("destinos_inventario.id"), nullable=False, index=True
    )
    estado: Mapped[StockEstado] = mapped_column(
        SAEnum(StockEstado, native_enum=False, length=20),
        nullable=False,
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    variante: Mapped["Variante"] = relationship()
    destino: Mapped[DestinoInventario] = relationship(back_populates="stocks")


class MovimientoStock(Base):
    __tablename__ = "movimientos_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    variante_id: Mapped[int] = mapped_column(ForeignKey("variantes.id"), nullable=False, index=True)
    destino_id: Mapped[int] = mapped_column(
        ForeignKey("destinos_inventario.id"), nullable=False, index=True
    )
    estado: Mapped[StockEstado] = mapped_column(
        SAEnum(StockEstado, native_enum=False, length=20),
        nullable=False,
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[MovimientoStockTipo] = mapped_column(
        SAEnum(MovimientoStockTipo, native_enum=False, length=30),
        nullable=False,
        index=True,
    )
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origen_tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    origen_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    referencia: Mapped[str | None] = mapped_column(String(120), nullable=True)
    motivo: Mapped[str | None] = mapped_column(String(180), nullable=True)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    saldo_anterior: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saldo_resultante: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    variante: Mapped["Variante"] = relationship()
    destino: Mapped[DestinoInventario] = relationship(back_populates="movimientos")
