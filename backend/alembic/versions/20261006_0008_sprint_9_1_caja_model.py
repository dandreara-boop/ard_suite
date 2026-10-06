"""sprint 9 1 modelo de caja

Revision ID: 20261006_0008
Revises: 20260924_0007
Create Date: 2026-10-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261006_0008"
down_revision: Union[str, Sequence[str], None] = "20260924_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


venta_estado = sa.Enum(
    "ABIERTA",
    "SUSPENDIDA",
    "LISTA_PARA_COBRAR",
    "EN_PAGO",
    "EN_COBRO",
    "CERRADA",
    "ANULADA",
    name="venta_estado",
    native_enum=False,
    length=20,
)
venta_tipo_atencion = sa.Enum("ATENDIDA", "AUTOSERVICIO", name="venta_tipo_atencion", native_enum=False, length=20)
sesion_caja_estado = sa.Enum("ABIERTA", "CERRADA", name="sesion_caja_estado", native_enum=False, length=20)
movimiento_caja_tipo = sa.Enum("INGRESO", "RETIRO", "EGRESO", "AJUSTE", name="movimiento_caja_tipo", native_enum=False, length=20)
arqueo_caja_tipo = sa.Enum("CONTROL", "CIERRE", name="arqueo_caja_tipo", native_enum=False, length=20)
arqueo_caja_estado = sa.Enum(
    "REGISTRADO",
    "CON_CORRECCION_SOLICITADA",
    "CORREGIDO",
    name="arqueo_caja_estado",
    native_enum=False,
    length=40,
)
solicitud_correccion_arqueo_estado = sa.Enum(
    "PENDIENTE",
    "APROBADA",
    "RECHAZADA",
    name="solicitud_correccion_arqueo_estado",
    native_enum=False,
    length=20,
)
posible_error_pago_estado = sa.Enum(
    "PENDIENTE",
    "DESCARTADO",
    "CONFIRMADO",
    "RESUELTO",
    name="posible_error_pago_estado",
    native_enum=False,
    length=20,
)
evento_operacion_venta_tipo = sa.Enum(
    "CREACION",
    "ENVIO_CAJA",
    "CAPTURA",
    "LIBERACION",
    "MODIFICACION",
    "CONFIRMACION",
    "ANULACION",
    name="evento_operacion_venta_tipo",
    native_enum=False,
    length=20,
)


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "cajas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("activa", sa.Boolean(), server_default="1", nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["destino_id"], ["destinos_inventario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cajas_codigo"), "cajas", ["codigo"], unique=True)
    op.create_index(op.f("ix_cajas_destino_id"), "cajas", ["destino_id"], unique=False)
    op.create_index(op.f("ix_cajas_global_id"), "cajas", ["global_id"], unique=True)

    op.create_table(
        "sesiones_caja",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("caja_id", sa.Integer(), nullable=False),
        sa.Column("cajero_id", sa.Integer(), nullable=False),
        sa.Column("abierta_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cerrada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("efectivo_inicial", sa.Numeric(12, 2), nullable=False),
        sa.Column("efectivo_final_declarado", sa.Numeric(12, 2), nullable=True),
        sa.Column("estado", sesion_caja_estado, server_default="ABIERTA", nullable=False),
        sa.Column("observacion_cierre", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["caja_id"], ["cajas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sesiones_caja_abierta_at"), "sesiones_caja", ["abierta_at"], unique=False)
    op.create_index(op.f("ix_sesiones_caja_caja_id"), "sesiones_caja", ["caja_id"], unique=False)
    op.create_index("ix_sesiones_caja_caja_estado", "sesiones_caja", ["caja_id", "estado"], unique=False)
    op.create_index(op.f("ix_sesiones_caja_cajero_id"), "sesiones_caja", ["cajero_id"], unique=False)
    op.create_index(op.f("ix_sesiones_caja_estado"), "sesiones_caja", ["estado"], unique=False)
    op.create_index(op.f("ix_sesiones_caja_global_id"), "sesiones_caja", ["global_id"], unique=True)

    # numero_corto remains nullable and non-unique; assignment policy is deferred to Sprint 9.2.
    op.add_column("ventas", sa.Column("numero_corto", sa.Integer(), nullable=True))
    op.add_column("ventas", sa.Column("referencia_cliente", sa.String(length=120), nullable=True))
    op.add_column("ventas", sa.Column("vendedor_id", sa.Integer(), nullable=True))
    op.add_column("ventas", sa.Column("tipo_atencion", venta_tipo_atencion, server_default="ATENDIDA", nullable=False))
    op.add_column("ventas", sa.Column("caja_captura_id", sa.Integer(), nullable=True))
    op.add_column("ventas", sa.Column("sesion_caja_id", sa.Integer(), nullable=True))
    op.add_column("ventas", sa.Column("capturada_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_ventas_caja_captura_id"), "ventas", ["caja_captura_id"], unique=False)
    op.create_index(op.f("ix_ventas_numero_corto"), "ventas", ["numero_corto"], unique=False)
    op.create_index(op.f("ix_ventas_sesion_caja_id"), "ventas", ["sesion_caja_id"], unique=False)
    op.create_index(op.f("ix_ventas_tipo_atencion"), "ventas", ["tipo_atencion"], unique=False)
    op.create_index(op.f("ix_ventas_vendedor_id"), "ventas", ["vendedor_id"], unique=False)
    op.create_foreign_key(op.f("fk_ventas_caja_captura_id_cajas"), "ventas", "cajas", ["caja_captura_id"], ["id"])
    op.create_foreign_key(op.f("fk_ventas_sesion_caja_id_sesiones_caja"), "ventas", "sesiones_caja", ["sesion_caja_id"], ["id"])

    op.create_table(
        "movimientos_caja",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("sesion_caja_id", sa.Integer(), nullable=False),
        sa.Column("tipo", movimiento_caja_tipo, nullable=False),
        sa.Column("importe", sa.Numeric(12, 2), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("referencia", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["sesion_caja_id"], ["sesiones_caja.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_movimientos_caja_fecha"), "movimientos_caja", ["fecha"], unique=False)
    op.create_index(op.f("ix_movimientos_caja_global_id"), "movimientos_caja", ["global_id"], unique=True)
    op.create_index(op.f("ix_movimientos_caja_sesion_caja_id"), "movimientos_caja", ["sesion_caja_id"], unique=False)
    op.create_index(op.f("ix_movimientos_caja_tipo"), "movimientos_caja", ["tipo"], unique=False)
    op.create_index(op.f("ix_movimientos_caja_usuario_id"), "movimientos_caja", ["usuario_id"], unique=False)

    op.create_table(
        "arqueos_caja",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("sesion_caja_id", sa.Integer(), nullable=False),
        sa.Column("tipo", arqueo_caja_tipo, server_default="CONTROL", nullable=False),
        sa.Column("efectivo_esperado", sa.Numeric(12, 2), nullable=True),
        sa.Column("primer_conteo", sa.Numeric(12, 2), nullable=False),
        sa.Column("diferencia", sa.Numeric(12, 2), nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("estado", arqueo_caja_estado, server_default="REGISTRADO", nullable=False),
        sa.ForeignKeyConstraint(["sesion_caja_id"], ["sesiones_caja.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_arqueos_caja_estado"), "arqueos_caja", ["estado"], unique=False)
    op.create_index(op.f("ix_arqueos_caja_fecha"), "arqueos_caja", ["fecha"], unique=False)
    op.create_index(op.f("ix_arqueos_caja_global_id"), "arqueos_caja", ["global_id"], unique=True)
    op.create_index(op.f("ix_arqueos_caja_sesion_caja_id"), "arqueos_caja", ["sesion_caja_id"], unique=False)
    op.create_index(op.f("ix_arqueos_caja_tipo"), "arqueos_caja", ["tipo"], unique=False)
    op.create_index(op.f("ix_arqueos_caja_usuario_id"), "arqueos_caja", ["usuario_id"], unique=False)

    op.create_table(
        "solicitudes_correccion_arqueo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("arqueo_id", sa.Integer(), nullable=False),
        sa.Column("conteo_original", sa.Numeric(12, 2), nullable=False),
        sa.Column("conteo_corregido_propuesto", sa.Numeric(12, 2), nullable=False),
        sa.Column("cajero_solicitante_id", sa.Integer(), nullable=False),
        sa.Column("solicitada_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("estado", solicitud_correccion_arqueo_estado, server_default="PENDIENTE", nullable=False),
        sa.Column("supervisor_id", sa.Integer(), nullable=True),
        sa.Column("resuelta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["arqueo_id"], ["arqueos_caja.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_solicitudes_correccion_arqueo_arqueo_id"), "solicitudes_correccion_arqueo", ["arqueo_id"], unique=False)
    op.create_index(op.f("ix_solicitudes_correccion_arqueo_cajero_solicitante_id"), "solicitudes_correccion_arqueo", ["cajero_solicitante_id"], unique=False)
    op.create_index(op.f("ix_solicitudes_correccion_arqueo_estado"), "solicitudes_correccion_arqueo", ["estado"], unique=False)
    op.create_index(op.f("ix_solicitudes_correccion_arqueo_global_id"), "solicitudes_correccion_arqueo", ["global_id"], unique=True)
    op.create_index(op.f("ix_solicitudes_correccion_arqueo_supervisor_id"), "solicitudes_correccion_arqueo", ["supervisor_id"], unique=False)

    op.create_table(
        "posibles_errores_pago",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("venta_id", sa.Integer(), nullable=False),
        sa.Column("sesion_caja_id", sa.Integer(), nullable=False),
        sa.Column("cajero_id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("estado", posible_error_pago_estado, server_default="PENDIENTE", nullable=False),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["sesion_caja_id"], ["sesiones_caja.id"]),
        sa.ForeignKeyConstraint(["venta_id"], ["ventas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_posibles_errores_pago_cajero_id"), "posibles_errores_pago", ["cajero_id"], unique=False)
    op.create_index(op.f("ix_posibles_errores_pago_estado"), "posibles_errores_pago", ["estado"], unique=False)
    op.create_index(op.f("ix_posibles_errores_pago_fecha"), "posibles_errores_pago", ["fecha"], unique=False)
    op.create_index(op.f("ix_posibles_errores_pago_global_id"), "posibles_errores_pago", ["global_id"], unique=True)
    op.create_index(op.f("ix_posibles_errores_pago_sesion_caja_id"), "posibles_errores_pago", ["sesion_caja_id"], unique=False)
    op.create_index(op.f("ix_posibles_errores_pago_venta_id"), "posibles_errores_pago", ["venta_id"], unique=False)

    op.create_table(
        "eventos_operacion_venta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=36), nullable=False),
        sa.Column("venta_id", sa.Integer(), nullable=False),
        sa.Column("tipo", evento_operacion_venta_tipo, nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("caja_id", sa.Integer(), nullable=True),
        sa.Column("sesion_caja_id", sa.Integer(), nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["caja_id"], ["cajas.id"]),
        sa.ForeignKeyConstraint(["sesion_caja_id"], ["sesiones_caja.id"]),
        sa.ForeignKeyConstraint(["venta_id"], ["ventas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("global_id", name="uq_evento_operacion_venta_global_id"),
    )
    op.create_index(op.f("ix_eventos_operacion_venta_caja_id"), "eventos_operacion_venta", ["caja_id"], unique=False)
    op.create_index(op.f("ix_eventos_operacion_venta_fecha"), "eventos_operacion_venta", ["fecha"], unique=False)
    op.create_index(op.f("ix_eventos_operacion_venta_sesion_caja_id"), "eventos_operacion_venta", ["sesion_caja_id"], unique=False)
    op.create_index(op.f("ix_eventos_operacion_venta_tipo"), "eventos_operacion_venta", ["tipo"], unique=False)
    op.create_index(op.f("ix_eventos_operacion_venta_usuario_id"), "eventos_operacion_venta", ["usuario_id"], unique=False)
    op.create_index(op.f("ix_eventos_operacion_venta_venta_id"), "eventos_operacion_venta", ["venta_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_eventos_operacion_venta_venta_id"), table_name="eventos_operacion_venta")
    op.drop_index(op.f("ix_eventos_operacion_venta_usuario_id"), table_name="eventos_operacion_venta")
    op.drop_index(op.f("ix_eventos_operacion_venta_tipo"), table_name="eventos_operacion_venta")
    op.drop_index(op.f("ix_eventos_operacion_venta_sesion_caja_id"), table_name="eventos_operacion_venta")
    op.drop_index(op.f("ix_eventos_operacion_venta_fecha"), table_name="eventos_operacion_venta")
    op.drop_index(op.f("ix_eventos_operacion_venta_caja_id"), table_name="eventos_operacion_venta")
    op.drop_table("eventos_operacion_venta")
    op.drop_index(op.f("ix_posibles_errores_pago_venta_id"), table_name="posibles_errores_pago")
    op.drop_index(op.f("ix_posibles_errores_pago_sesion_caja_id"), table_name="posibles_errores_pago")
    op.drop_index(op.f("ix_posibles_errores_pago_global_id"), table_name="posibles_errores_pago")
    op.drop_index(op.f("ix_posibles_errores_pago_fecha"), table_name="posibles_errores_pago")
    op.drop_index(op.f("ix_posibles_errores_pago_estado"), table_name="posibles_errores_pago")
    op.drop_index(op.f("ix_posibles_errores_pago_cajero_id"), table_name="posibles_errores_pago")
    op.drop_table("posibles_errores_pago")
    op.drop_index(op.f("ix_solicitudes_correccion_arqueo_supervisor_id"), table_name="solicitudes_correccion_arqueo")
    op.drop_index(op.f("ix_solicitudes_correccion_arqueo_global_id"), table_name="solicitudes_correccion_arqueo")
    op.drop_index(op.f("ix_solicitudes_correccion_arqueo_estado"), table_name="solicitudes_correccion_arqueo")
    op.drop_index(op.f("ix_solicitudes_correccion_arqueo_cajero_solicitante_id"), table_name="solicitudes_correccion_arqueo")
    op.drop_index(op.f("ix_solicitudes_correccion_arqueo_arqueo_id"), table_name="solicitudes_correccion_arqueo")
    op.drop_table("solicitudes_correccion_arqueo")
    op.drop_index(op.f("ix_arqueos_caja_usuario_id"), table_name="arqueos_caja")
    op.drop_index(op.f("ix_arqueos_caja_tipo"), table_name="arqueos_caja")
    op.drop_index(op.f("ix_arqueos_caja_sesion_caja_id"), table_name="arqueos_caja")
    op.drop_index(op.f("ix_arqueos_caja_global_id"), table_name="arqueos_caja")
    op.drop_index(op.f("ix_arqueos_caja_fecha"), table_name="arqueos_caja")
    op.drop_index(op.f("ix_arqueos_caja_estado"), table_name="arqueos_caja")
    op.drop_table("arqueos_caja")
    op.drop_index(op.f("ix_movimientos_caja_usuario_id"), table_name="movimientos_caja")
    op.drop_index(op.f("ix_movimientos_caja_tipo"), table_name="movimientos_caja")
    op.drop_index(op.f("ix_movimientos_caja_sesion_caja_id"), table_name="movimientos_caja")
    op.drop_index(op.f("ix_movimientos_caja_global_id"), table_name="movimientos_caja")
    op.drop_index(op.f("ix_movimientos_caja_fecha"), table_name="movimientos_caja")
    op.drop_table("movimientos_caja")
    op.drop_constraint(op.f("fk_ventas_sesion_caja_id_sesiones_caja"), "ventas", type_="foreignkey")
    op.drop_constraint(op.f("fk_ventas_caja_captura_id_cajas"), "ventas", type_="foreignkey")
    op.drop_index(op.f("ix_ventas_vendedor_id"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_tipo_atencion"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_sesion_caja_id"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_numero_corto"), table_name="ventas")
    op.drop_index(op.f("ix_ventas_caja_captura_id"), table_name="ventas")
    op.drop_column("ventas", "capturada_at")
    op.drop_column("ventas", "sesion_caja_id")
    op.drop_column("ventas", "caja_captura_id")
    op.drop_column("ventas", "tipo_atencion")
    op.drop_column("ventas", "vendedor_id")
    op.drop_column("ventas", "referencia_cliente")
    op.drop_column("ventas", "numero_corto")
    op.drop_index(op.f("ix_sesiones_caja_global_id"), table_name="sesiones_caja")
    op.drop_index(op.f("ix_sesiones_caja_estado"), table_name="sesiones_caja")
    op.drop_index(op.f("ix_sesiones_caja_cajero_id"), table_name="sesiones_caja")
    op.drop_index("ix_sesiones_caja_caja_estado", table_name="sesiones_caja")
    op.drop_index(op.f("ix_sesiones_caja_caja_id"), table_name="sesiones_caja")
    op.drop_index(op.f("ix_sesiones_caja_abierta_at"), table_name="sesiones_caja")
    op.drop_table("sesiones_caja")
    op.drop_index(op.f("ix_cajas_global_id"), table_name="cajas")
    op.drop_index(op.f("ix_cajas_destino_id"), table_name="cajas")
    op.drop_index(op.f("ix_cajas_codigo"), table_name="cajas")
    op.drop_table("cajas")
