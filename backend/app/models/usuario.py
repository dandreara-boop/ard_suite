from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func, inspect
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, validates

from backend.app.db.base import Base
from backend.app.core.identidad import normalizar_nombre_usuario
from backend.app.models.catalogo import TimestampMixin


class RolUsuario(str, Enum):
    ADMINISTRADOR = "ADMINISTRADOR"
    SUPERVISOR = "SUPERVISOR"
    CAJERO = "CAJERO"


class Usuario(TimestampMixin, Base):
    __tablename__ = "usuarios"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_usuarios_version_positiva"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    nombre_usuario: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    nombre_visible: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    rol: Mapped[RolUsuario] = mapped_column(
        SAEnum(RolUsuario, native_enum=False, length=20, name="rol_usuario", create_constraint=True, validate_strings=True), nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # Optimistic versioning covers future role, credential and activation edits.
    # It is not a central/local synchronization protocol.
    __mapper_args__ = {"version_id_col": version}

    @validates("nombre_usuario")
    def normalize_username(self, key: str, value: str) -> str:
        return normalizar_nombre_usuario(value)

    @validates("global_id")
    def preserve_global_identity(self, key: str, value: str) -> str:
        value = str(UUID(value))
        if inspect(self).persistent and self.global_id != value:
            raise ValueError("El identificador global de una identidad persistida es inmutable")
        return value


class InicializacionIdentidad(Base):
    """Durable singleton: deleting/deactivating accounts cannot reopen bootstrap."""
    __tablename__ = "inicializacion_identidad"
    __table_args__ = (CheckConstraint("id = 1", name="ck_inicializacion_identidad_unica"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    inicializada_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
