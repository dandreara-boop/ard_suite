from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.catalogo import TimestampMixin


class SesionCajaEstado(str, Enum):
    ABIERTA = "ABIERTA"
    CERRADA = "CERRADA"


class MovimientoCajaTipo(str, Enum):
    INGRESO = "INGRESO"
    RETIRO = "RETIRO"
    EGRESO = "EGRESO"
    AJUSTE = "AJUSTE"


class ArqueoCajaTipo(str, Enum):
    CONTROL = "CONTROL"
    CIERRE = "CIERRE"


class ArqueoCajaEstado(str, Enum):
    REGISTRADO = "REGISTRADO"
    CON_CORRECCION_SOLICITADA = "CON_CORRECCION_SOLICITADA"
    CORREGIDO = "CORREGIDO"


class SolicitudCorreccionArqueoEstado(str, Enum):
    PENDIENTE = "PENDIENTE"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"


class PosibleErrorPagoEstado(str, Enum):
    PENDIENTE = "PENDIENTE"
    DESCARTADO = "DESCARTADO"
    CONFIRMADO = "CONFIRMADO"
    RESUELTO = "RESUELTO"


class EventoOperacionVentaTipo(str, Enum):
    CREACION = "CREACION"
    ENVIO_CAJA = "ENVIO_CAJA"
    CAPTURA = "CAPTURA"
    LIBERACION = "LIBERACION"
    MODIFICACION = "MODIFICACION"
    CONFIRMACION = "CONFIRMACION"
    ANULACION = "ANULACION"


class Caja(TimestampMixin, Base):
    __tablename__ = "cajas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos_inventario.id"), nullable=False, index=True)
    codigo: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    destino: Mapped["DestinoInventario"] = relationship()
    sesiones: Mapped[list["SesionCaja"]] = relationship(back_populates="caja")


class SesionCaja(TimestampMixin, Base):
    __tablename__ = "sesiones_caja"
    __table_args__ = (Index("ix_sesiones_caja_caja_estado", "caja_id", "estado"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    caja_id: Mapped[int] = mapped_column(ForeignKey("cajas.id"), nullable=False, index=True)
    cajero_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    abierta_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    cerrada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    efectivo_inicial: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    efectivo_final_declarado: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    estado: Mapped[SesionCajaEstado] = mapped_column(
        SAEnum(SesionCajaEstado, native_enum=False, length=20),
        nullable=False,
        default=SesionCajaEstado.ABIERTA,
        server_default=SesionCajaEstado.ABIERTA.value,
        index=True,
    )
    observacion_cierre: Mapped[str | None] = mapped_column(Text, nullable=True)

    caja: Mapped[Caja] = relationship(back_populates="sesiones")
    movimientos: Mapped[list["MovimientoCaja"]] = relationship(back_populates="sesion_caja")
    arqueos: Mapped[list["ArqueoCaja"]] = relationship(back_populates="sesion_caja")


class MovimientoCaja(Base):
    __tablename__ = "movimientos_caja"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    sesion_caja_id: Mapped[int] = mapped_column(ForeignKey("sesiones_caja.id"), nullable=False, index=True)
    tipo: Mapped[MovimientoCajaTipo] = mapped_column(
        SAEnum(MovimientoCajaTipo, native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    importe: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    referencia: Mapped[str | None] = mapped_column(String(120), nullable=True)

    sesion_caja: Mapped[SesionCaja] = relationship(back_populates="movimientos")


class ArqueoCaja(Base):
    __tablename__ = "arqueos_caja"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    sesion_caja_id: Mapped[int] = mapped_column(ForeignKey("sesiones_caja.id"), nullable=False, index=True)
    tipo: Mapped[ArqueoCajaTipo] = mapped_column(
        SAEnum(ArqueoCajaTipo, native_enum=False, length=20),
        nullable=False,
        default=ArqueoCajaTipo.CONTROL,
        server_default=ArqueoCajaTipo.CONTROL.value,
        index=True,
    )
    efectivo_esperado: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    primer_conteo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    diferencia: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    estado: Mapped[ArqueoCajaEstado] = mapped_column(
        SAEnum(ArqueoCajaEstado, native_enum=False, length=40),
        nullable=False,
        default=ArqueoCajaEstado.REGISTRADO,
        server_default=ArqueoCajaEstado.REGISTRADO.value,
        index=True,
    )

    sesion_caja: Mapped[SesionCaja] = relationship(back_populates="arqueos")
    solicitudes_correccion: Mapped[list["SolicitudCorreccionArqueo"]] = relationship(back_populates="arqueo")


class SolicitudCorreccionArqueo(Base):
    __tablename__ = "solicitudes_correccion_arqueo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    arqueo_id: Mapped[int] = mapped_column(ForeignKey("arqueos_caja.id"), nullable=False, index=True)
    conteo_original: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    conteo_corregido_propuesto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cajero_solicitante_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    solicitada_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    estado: Mapped[SolicitudCorreccionArqueoEstado] = mapped_column(
        SAEnum(SolicitudCorreccionArqueoEstado, native_enum=False, length=20),
        nullable=False,
        default=SolicitudCorreccionArqueoEstado.PENDIENTE,
        server_default=SolicitudCorreccionArqueoEstado.PENDIENTE.value,
        index=True,
    )
    supervisor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resuelta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    arqueo: Mapped[ArqueoCaja] = relationship(back_populates="solicitudes_correccion")


class PosibleErrorPago(Base):
    __tablename__ = "posibles_errores_pago"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4())
    )
    venta_id: Mapped[int] = mapped_column(ForeignKey("ventas.id"), nullable=False, index=True)
    sesion_caja_id: Mapped[int] = mapped_column(ForeignKey("sesiones_caja.id"), nullable=False, index=True)
    cajero_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    estado: Mapped[PosibleErrorPagoEstado] = mapped_column(
        SAEnum(PosibleErrorPagoEstado, native_enum=False, length=20),
        nullable=False,
        default=PosibleErrorPagoEstado.PENDIENTE,
        server_default=PosibleErrorPagoEstado.PENDIENTE.value,
        index=True,
    )
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    venta: Mapped["Venta"] = relationship()
    sesion_caja: Mapped[SesionCaja] = relationship()


class EventoOperacionVenta(Base):
    __tablename__ = "eventos_operacion_venta"
    __table_args__ = (
        UniqueConstraint("global_id", name="uq_evento_operacion_venta_global_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(String(36), nullable=False, default=lambda: str(uuid4()))
    venta_id: Mapped[int] = mapped_column(ForeignKey("ventas.id"), nullable=False, index=True)
    tipo: Mapped[EventoOperacionVentaTipo] = mapped_column(
        SAEnum(EventoOperacionVentaTipo, native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    caja_id: Mapped[int | None] = mapped_column(ForeignKey("cajas.id"), nullable=True, index=True)
    sesion_caja_id: Mapped[int | None] = mapped_column(ForeignKey("sesiones_caja.id"), nullable=True, index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    venta: Mapped["Venta"] = relationship()
    caja: Mapped[Caja | None] = relationship()
    sesion_caja: Mapped[SesionCaja | None] = relationship()
