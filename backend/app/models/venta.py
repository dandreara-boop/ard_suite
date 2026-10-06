from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.catalogo import TimestampMixin


class VentaEstado(str, Enum):
    ABIERTA = "ABIERTA"
    SUSPENDIDA = "SUSPENDIDA"
    LISTA_PARA_COBRAR = "LISTA_PARA_COBRAR"
    EN_PAGO = "EN_PAGO"
    EN_COBRO = "EN_COBRO"
    CERRADA = "CERRADA"
    ANULADA = "ANULADA"


class VentaTipoAtencion(str, Enum):
    ATENDIDA = "ATENDIDA"
    AUTOSERVICIO = "AUTOSERVICIO"


class EventoPendienteEstado(str, Enum):
    PENDIENTE = "PENDIENTE"
    PROCESANDO = "PROCESANDO"
    PROCESADO = "PROCESADO"
    ERROR = "ERROR"


class Venta(TimestampMixin, Base):
    __tablename__ = "ventas"
    __table_args__ = (
        UniqueConstraint("destino_id", "numero_corto", name="uq_ventas_destino_numero_corto"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    numero_venta: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    # Assigned when the sale enters the cash-desk queue; never recycled after cancellation.
    numero_corto: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    referencia_cliente: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos_inventario.id"), nullable=False, index=True)
    estado: Mapped[VentaEstado] = mapped_column(
        SAEnum(VentaEstado, native_enum=False, length=20),
        nullable=False,
        default=VentaEstado.ABIERTA,
        server_default=VentaEstado.ABIERTA.value,
        index=True,
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vendedor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tipo_atencion: Mapped[VentaTipoAtencion] = mapped_column(
        SAEnum(VentaTipoAtencion, native_enum=False, length=20),
        nullable=False,
        default=VentaTipoAtencion.ATENDIDA,
        server_default=VentaTipoAtencion.ATENDIDA.value,
        index=True,
    )
    caja_captura_id: Mapped[int | None] = mapped_column(ForeignKey("cajas.id"), nullable=True, index=True)
    sesion_caja_id: Mapped[int | None] = mapped_column(ForeignKey("sesiones_caja.id"), nullable=True, index=True)
    capturada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cerrada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    destino: Mapped["DestinoInventario"] = relationship()
    caja_captura: Mapped["Caja | None"] = relationship()
    sesion_caja: Mapped["SesionCaja | None"] = relationship()
    detalles: Mapped[list["DetalleVenta"]] = relationship(
        back_populates="venta", cascade="all, delete-orphan"
    )
    pagos: Mapped[list["PagoVenta"]] = relationship(back_populates="venta", cascade="all, delete-orphan")


class SecuenciaNumeroCortoVenta(Base):
    __tablename__ = "secuencias_numero_corto_venta"

    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos_inventario.id"), primary_key=True)
    ultimo_numero: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    destino: Mapped["DestinoInventario"] = relationship()


class DetalleVenta(Base):
    __tablename__ = "detalles_venta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venta_id: Mapped[int] = mapped_column(ForeignKey("ventas.id"), nullable=False, index=True)
    variante_id: Mapped[int] = mapped_column(ForeignKey("variantes.id"), nullable=False, index=True)
    codigo_articulo: Mapped[str] = mapped_column(String(60), nullable=False)
    codigo_barra: Mapped[str] = mapped_column(String(80), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(240), nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    importe: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    venta: Mapped[Venta] = relationship(back_populates="detalles")
    variante: Mapped["Variante"] = relationship()


class PagoVenta(Base):
    __tablename__ = "pagos_venta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venta_id: Mapped[int] = mapped_column(ForeignKey("ventas.id"), nullable=False, index=True)
    medio_pago: Mapped[str] = mapped_column(String(60), nullable=False)
    importe: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    venta: Mapped[Venta] = relationship(back_populates="pagos")


class EventoPendiente(Base):
    __tablename__ = "eventos_pendientes"
    __table_args__ = (
        UniqueConstraint("tipo", "aggregate_type", "aggregate_id", name="uq_evento_pendiente_aggregate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    tipo: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    estado: Mapped[EventoPendienteEstado] = mapped_column(
        SAEnum(EventoPendienteEstado, native_enum=False, length=20),
        nullable=False,
        default=EventoPendienteEstado.PENDIENTE,
        server_default=EventoPendienteEstado.PENDIENTE.value,
        index=True,
    )
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
