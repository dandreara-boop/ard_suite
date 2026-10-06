from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.db.base import Base
from backend.app.models import (
    ArqueoCaja,
    ArqueoCajaEstado,
    ArqueoCajaTipo,
    Caja,
    DestinoInventario,
    DestinoInventarioTipo,
    EventoOperacionVenta,
    EventoOperacionVentaTipo,
    MovimientoCaja,
    MovimientoCajaTipo,
    PagoVenta,
    PosibleErrorPago,
    PosibleErrorPagoEstado,
    SesionCaja,
    SesionCajaEstado,
    SolicitudCorreccionArqueo,
    SolicitudCorreccionArqueoEstado,
    Venta,
    VentaEstado,
    VentaTipoAtencion,
)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def seed_cash_base(db: Session) -> tuple[DestinoInventario, Caja, SesionCaja]:
    destino = DestinoInventario(codigo="LOCAL_CAJA", nombre="Local Caja", tipo=DestinoInventarioTipo.LOCAL)
    caja = Caja(destino=destino, codigo="CAJA_1", nombre="Caja 1")
    sesion = SesionCaja(caja=caja, cajero_id=101, efectivo_inicial=Decimal("1500.25"))
    db.add_all([destino, caja, sesion])
    db.commit()
    return destino, caja, sesion


def test_tablas_sprint_9_1_existen_en_metadata(db_session: Session) -> None:
    table_names = set(inspect(db_session.bind).get_table_names())  # type: ignore[arg-type]

    assert {
        "cajas",
        "sesiones_caja",
        "movimientos_caja",
        "arqueos_caja",
        "solicitudes_correccion_arqueo",
        "posibles_errores_pago",
        "eventos_operacion_venta",
    }.issubset(table_names)
    assert "correcciones_administrativas_pago" not in table_names


def test_caja_sesion_y_movimiento_conservan_decimal_y_relaciones(db_session: Session) -> None:
    _, caja, sesion = seed_cash_base(db_session)
    movimiento = MovimientoCaja(
        sesion_caja=sesion,
        tipo=MovimientoCajaTipo.INGRESO,
        importe=Decimal("123.45"),
        usuario_id=101,
    )
    db_session.add(movimiento)
    db_session.commit()

    persisted = db_session.scalar(select(SesionCaja).where(SesionCaja.id == sesion.id))

    assert persisted is not None
    assert persisted.caja_id == caja.id
    assert persisted.caja.destino.codigo == "LOCAL_CAJA"
    assert persisted.estado == SesionCajaEstado.ABIERTA
    assert persisted.efectivo_inicial == Decimal("1500.25")
    assert persisted.movimientos[0].importe == Decimal("123.45")


def test_venta_acepta_nuevos_datos_y_autoservicio_sin_vendedor(db_session: Session) -> None:
    destino, caja, sesion = seed_cash_base(db_session)
    legacy = Venta(numero_venta="V-LEGACY", destino=destino)
    autoservicio = Venta(
        numero_venta="V-AUTO",
        numero_corto=12,
        referencia_cliente="Mesa 4",
        destino=destino,
        estado=VentaEstado.LISTA_PARA_COBRAR,
        tipo_atencion=VentaTipoAtencion.AUTOSERVICIO,
        vendedor_id=None,
        caja_captura=caja,
        sesion_caja=sesion,
    )
    db_session.add_all([legacy, autoservicio])
    db_session.commit()

    persisted = db_session.scalar(select(Venta).where(Venta.numero_venta == "V-AUTO"))

    assert legacy.tipo_atencion == VentaTipoAtencion.ATENDIDA
    assert persisted is not None
    assert persisted.numero_corto == 12
    assert persisted.referencia_cliente == "Mesa 4"
    assert persisted.vendedor_id is None
    assert persisted.tipo_atencion == VentaTipoAtencion.AUTOSERVICIO
    assert persisted.estado == VentaEstado.LISTA_PARA_COBRAR
    assert persisted.caja_captura_id == caja.id
    assert persisted.sesion_caja_id == sesion.id


def test_arqueo_conserva_primer_conteo_en_solicitud_de_correccion(db_session: Session) -> None:
    _, _, sesion = seed_cash_base(db_session)
    arqueo = ArqueoCaja(
        sesion_caja=sesion,
        tipo=ArqueoCajaTipo.CIERRE,
        efectivo_esperado=Decimal("1000.00"),
        primer_conteo=Decimal("950.00"),
        diferencia=Decimal("-50.00"),
        usuario_id=101,
    )
    solicitud = SolicitudCorreccionArqueo(
        arqueo=arqueo,
        conteo_original=Decimal("950.00"),
        conteo_corregido_propuesto=Decimal("990.00"),
        cajero_solicitante_id=101,
    )
    db_session.add_all([arqueo, solicitud])
    db_session.commit()

    persisted = db_session.scalar(select(SolicitudCorreccionArqueo))

    assert persisted is not None
    assert persisted.estado == SolicitudCorreccionArqueoEstado.PENDIENTE
    assert persisted.conteo_original == Decimal("950.00")
    assert persisted.arqueo.primer_conteo == Decimal("950.00")
    assert persisted.arqueo.estado == ArqueoCajaEstado.REGISTRADO


def test_posible_error_pago_referencia_venta_y_sesion_sin_modificar_pago(db_session: Session) -> None:
    destino, _, sesion = seed_cash_base(db_session)
    venta = Venta(numero_venta="V-ERROR-PAGO", destino=destino, estado=VentaEstado.CERRADA)
    pago = PagoVenta(venta=venta, medio_pago="EFECTIVO", importe=Decimal("250.00"))
    posible_error = PosibleErrorPago(
        venta=venta,
        sesion_caja=sesion,
        cajero_id=101,
        observacion="Cajero sospecha medio incorrecto",
    )
    db_session.add_all([venta, pago, posible_error])
    db_session.commit()

    persisted_pago = db_session.scalar(select(PagoVenta).where(PagoVenta.id == pago.id))
    persisted_error = db_session.scalar(select(PosibleErrorPago))

    assert persisted_error is not None
    assert persisted_error.estado == PosibleErrorPagoEstado.PENDIENTE
    assert persisted_error.venta_id == venta.id
    assert persisted_error.sesion_caja_id == sesion.id
    assert persisted_pago is not None
    assert persisted_pago.medio_pago == "EFECTIVO"
    assert persisted_pago.importe == Decimal("250.00")


def test_evento_operacion_registra_contexto_sin_correccion_administrativa(db_session: Session) -> None:
    destino, caja, sesion = seed_cash_base(db_session)
    venta = Venta(numero_venta="V-AUDIT", destino=destino, estado=VentaEstado.CERRADA)
    pago = PagoVenta(venta=venta, medio_pago="EFECTIVO", importe=Decimal("300.00"))
    posible_error = PosibleErrorPago(venta=venta, sesion_caja=sesion, cajero_id=101)
    evento = EventoOperacionVenta(
        venta=venta,
        tipo=EventoOperacionVentaTipo.CAPTURA,
        usuario_id=101,
        caja=caja,
        sesion_caja=sesion,
        payload={"estado": "EN_COBRO"},
    )
    db_session.add_all([venta, pago, posible_error, evento])
    db_session.commit()

    persisted_event = db_session.scalar(select(EventoOperacionVenta))
    persisted_pago = db_session.scalar(select(PagoVenta).where(PagoVenta.id == pago.id))

    assert persisted_event is not None
    assert persisted_event.tipo == EventoOperacionVentaTipo.CAPTURA
    assert persisted_event.payload == {"estado": "EN_COBRO"}
    assert persisted_pago is not None
    assert persisted_pago.medio_pago == "EFECTIVO"
    assert posible_error.estado == PosibleErrorPagoEstado.PENDIENTE
