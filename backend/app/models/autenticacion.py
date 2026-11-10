from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.sesiones import UTCDateTime
from backend.app.db.base import Base


class SesionUsuario(Base):
    __tablename__ = "sesiones_usuario"
    __table_args__ = (
        CheckConstraint("estado IN ('ACTIVA','BLOQUEADA','CERRADA','REVOCADA')", name="ck_sesion_usuario_estado"),
        CheckConstraint("generacion >= 1", name="ck_sesion_usuario_generacion"),
        Index("ix_sesion_usuario_usuario_estado", "usuario_id", "estado"),
        {"mysql_engine": "InnoDB"},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False)
    usuario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sucursal: Mapped[str | None] = mapped_column(String(64))
    puesto: Mapped[str | None] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(12), nullable=False)
    generacion: Mapped[int] = mapped_column(Integer, nullable=False)
    creada_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actividad_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    bloqueada_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finalizada_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    motivo: Mapped[str | None] = mapped_column(String(32))


class CredencialSesion(Base):
    """One access/refresh pair per rotation; no recoverable token material."""
    __tablename__ = "credenciales_sesion"
    __table_args__ = (
        CheckConstraint("estado IN ('PENDIENTE','ACTIVA','INVALIDADA')", name="ck_credencial_sesion_estado"),
        CheckConstraint("generacion >= 1 AND origen_generacion >= 0", name="ck_credencial_sesion_generacion"),
        Index("ix_credencial_sesion_sesion_estado", "sesion_id", "estado"),
        {"mysql_engine": "InnoDB"},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rotacion_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    sesion_id: Mapped[int] = mapped_column(ForeignKey("sesiones_usuario.id"), nullable=False)
    generacion: Mapped[int] = mapped_column(Integer, nullable=False)
    origen_generacion: Mapped[int] = mapped_column(Integer, nullable=False)
    acceso_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    renovacion_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    estado: Mapped[str] = mapped_column(String(12), nullable=False)
    creada_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmar_hasta: Mapped[datetime | None] = mapped_column(UTCDateTime())
    activada_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acceso_hasta: Mapped[datetime | None] = mapped_column(UTCDateTime())
    renovacion_hasta: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalidada_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ConfiguracionSeguridad(Base):
    __tablename__ = "configuracion_seguridad"
    __table_args__ = (
        UniqueConstraint("ambito", "clave", "parametro", name="uq_config_seguridad_ambito_clave_parametro"),
        CheckConstraint("ambito IN ('GENERAL','SUCURSAL','PUESTO')", name="ck_config_seguridad_ambito"),
        CheckConstraint("(ambito = 'GENERAL' AND clave = '') OR (ambito <> 'GENERAL' AND clave <> '')", name="ck_config_seguridad_clave"),
        CheckConstraint("valor > 0 AND version >= 1", name="ck_config_seguridad_valor_version"),
        {"mysql_engine": "InnoDB"},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ambito: Mapped[str] = mapped_column(String(8), nullable=False)
    clave: Mapped[str] = mapped_column(String(64), nullable=False)
    parametro: Mapped[str] = mapped_column(String(40), nullable=False)
    valor: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LimiteAutenticacion(Base):
    __tablename__ = "limites_autenticacion"
    __table_args__ = (
        CheckConstraint("intentos >= 0 AND generacion >= 1", name="ck_limite_autenticacion_intentos"),
        {"mysql_engine": "InnoDB"},
    )
    clave: Mapped[str] = mapped_column(String(64), primary_key=True)
    intentos: Mapped[int] = mapped_column(Integer, nullable=False)
    generacion: Mapped[int] = mapped_column(Integer, nullable=False)
    ultimo_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ultimo_fallo_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditoriaAutenticacion(Base):
    __tablename__ = "auditoria_autenticacion"
    __table_args__ = {"mysql_engine": "InnoDB"}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evento: Mapped[str] = mapped_column(String(32), nullable=False)
    fecha: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    sesion_id: Mapped[int | None] = mapped_column(ForeignKey("sesiones_usuario.id"))
    origen_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(32))
