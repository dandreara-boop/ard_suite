"""Continuidad entre turnos; todas las escrituras usan SQLite en memoria."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.models import (
    ArqueoCaja, MovimientoCaja, PagoVenta, SesionCaja, SesionCajaEstado,
    SolicitudCorreccionArqueo, SolicitudCorreccionArqueoEstado, Venta, VentaEstado,
)
from backend.app.schemas.caja import (
    CerrarSesionCajaRequest, MovimientoCajaCreate, SolicitarCorreccionArqueoRequest,
)
from backend.app.schemas.commercial import ResolucionComercialRequest
from backend.app.services.caja_service import CajaService
from backend.app.services.commercial_service import CommercialService
from backend.app.services.exceptions import ConflictError, ValidationError
from backend.tests.test_sprint_9_5a_efectivo import (
    create_captured_sale, db_session, setup_cash_sales,  # noqa: F401
)


def snapshot(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def correction():
    return SolicitarCorreccionArqueoRequest(
        usuario_id=100, importe_corregido=Decimal("30010.00"),
        motivo="Revisar conteo del turno anterior",
    )


def movement(usuario_id):
    return MovimientoCajaCreate(usuario_id=usuario_id, tipo="INGRESO", importe=Decimal("10.00"))


def collect_sale(db, ctx, sesion):
    venta = create_captured_sale(db, ctx, sesion=sesion)
    CommercialService(db).confirmar_resolucion(
        venta.id,
        ResolucionComercialRequest(
            pagos=[{"medio_pago_id": ctx["efectivo"].id, "tipo": "RESTO"}],
            usuario_id=sesion.cajero_id,
        ),
    )
    db.refresh(venta)
    assert venta.estado == VentaEstado.CERRADA
    assert venta.sesion_caja_id == sesion.id
    assert venta.caja_captura_id == sesion.caja_id
    pagos = db.scalars(select(PagoVenta).where(PagoVenta.venta_id == venta.id)).all()
    assert len(pagos) == 1
    assert pagos[0].importe == Decimal("30000.00")
    assert pagos[0].medio_pago_id == ctx["efectivo"].id
    return venta, pagos[0]


@pytest.mark.parametrize("diferencia,estado,observacion", [
    pytest.param("0.00", None, None, id="sin-diferencia"),
    pytest.param("25.00", None, None, id="diferencia-positiva"),
    pytest.param("-25.00", None, None, id="diferencia-negativa"),
    pytest.param("-25.00", SolicitudCorreccionArqueoEstado.PENDIENTE, None, id="pendiente"),
    pytest.param("-25.00", SolicitudCorreccionArqueoEstado.APROBADA, None, id="aprobada"),
    pytest.param("-25.00", SolicitudCorreccionArqueoEstado.RECHAZADA, None, id="rechazada"),
    pytest.param("-25.00", None, "Posible error en el conteo del cierre", id="observacion"),
])
def test_siguiente_turno_opera_sin_alterar_historial(
    db_session: Session, diferencia, estado, observacion,
):
    ctx = setup_cash_sales(db_session)
    service = CajaService(db_session)
    anterior = ctx["sesion"]
    venta, pago = collect_sale(db_session, ctx, anterior)
    movimiento = service.registrar_movimiento(anterior.id, movement(100))
    conteo = Decimal("30010.00") + Decimal(diferencia)
    cierre = service.cerrar_sesion(
        anterior.id,
        CerrarSesionCajaRequest(
            usuario_id=100, efectivo_final_declarado=conteo, observacion_cierre=observacion,
        ),
    )
    assert cierre.efectivo_esperado == Decimal("30010.00")
    assert cierre.primer_conteo == conteo
    assert cierre.diferencia == Decimal(diferencia)
    arqueo = db_session.get(ArqueoCaja, cierre.arqueo_id)
    solicitud = None
    if estado is not None:
        respuesta = service.solicitar_correccion_arqueo(arqueo.id, correction())
        solicitud = db_session.get(SolicitudCorreccionArqueo, respuesta.solicitud_id)
        if estado != SolicitudCorreccionArqueoEstado.PENDIENTE:
            # Preparacion de datos: no se implementa resolucion del supervisor.
            solicitud.estado = estado
            solicitud.supervisor_id = 900
            solicitud.resuelta_at = datetime(2026, 10, 16, 12)
            solicitud.observacion = "Estado preparado exclusivamente para esta prueba"
            db_session.commit()
        # Una rechazada tampoco permite un segundo intento antes del nuevo turno.
        with pytest.raises(ConflictError, match="ya tiene una solicitud"):
            service.solicitar_correccion_arqueo(arqueo.id, correction())

    db_session.expire_all()
    historicos = [anterior, arqueo, venta, pago, movimiento]
    if solicitud is not None:
        historicos.append(solicitud)
    antes = [snapshot(row) for row in historicos]
    assert anterior.estado == SesionCajaEstado.CERRADA
    assert anterior.observacion_cierre == observacion

    nueva = service.abrir_sesion(
        anterior.caja_id, cajero_id=101, efectivo_inicial=Decimal("500.00"),
    ).sesion
    assert nueva.id != anterior.id
    assert nueva.caja_id == anterior.caja_id
    assert nueva.cajero_id == 101
    assert nueva.estado == SesionCajaEstado.ABIERTA
    assert nueva.cerrada_at is None
    assert service.listar_movimientos(nueva.id) == []

    # El rechazo tardio no revierte ni cierra el turno ya confirmado.
    with pytest.raises(ConflictError, match="plazo termino"):
        service.solicitar_correccion_arqueo(arqueo.id, correction())
    assert service.get_sesion(nueva.id).estado == SesionCajaEstado.ABIERTA
    service.registrar_movimiento(nueva.id, movement(101))
    collect_sale(db_session, ctx, db_session.get(SesionCaja, nueva.id))
    assert service.total_pagos_efectivo_sesion(nueva.id) == Decimal("30000.00")

    # El turno cerrado sigue siendo inoperable, aun con el siguiente abierto.
    with pytest.raises(ValidationError, match="no esta abierta"):
        service.registrar_movimiento(anterior.id, movement(100))
    db_session.expire_all()
    assert [snapshot(row) for row in historicos] == antes
    assert db_session.get(SesionCaja, nueva.id).estado == SesionCajaEstado.ABIERTA
    assert len(service.listar_movimientos(anterior.id)) == 1
    assert len(service.listar_movimientos(nueva.id)) == 1
    assert len(db_session.scalars(select(MovimientoCaja)).all()) == 2
    assert len(db_session.scalars(select(ArqueoCaja)).all()) == 1
    assert len(db_session.scalars(select(Venta)).all()) == 2
    assert len(db_session.scalars(select(PagoVenta)).all()) == 2
    assert len(db_session.scalars(select(SolicitudCorreccionArqueo)).all()) == (estado is not None)


def test_plazo_no_revive_al_cerrar_el_turno_posterior(db_session: Session):
    ctx = setup_cash_sales(db_session)
    service = CajaService(db_session)
    anterior = ctx["sesion"]
    cierre = service.cerrar_sesion(
        anterior.id, CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("0")),
    )
    arqueo = db_session.get(ArqueoCaja, cierre.arqueo_id)
    antes = snapshot(arqueo)
    nueva = service.abrir_sesion(anterior.caja_id, cajero_id=101, efectivo_inicial=Decimal("0")).sesion
    service.cerrar_sesion(
        nueva.id, CerrarSesionCajaRequest(usuario_id=101, efectivo_final_declarado=Decimal("0")),
    )
    with pytest.raises(ConflictError, match="plazo termino"):
        service.solicitar_correccion_arqueo(arqueo.id, correction())
    tercera = service.abrir_sesion(anterior.caja_id, cajero_id=102, efectivo_inicial=Decimal("0")).sesion
    collect_sale(db_session, ctx, db_session.get(SesionCaja, tercera.id))
    db_session.expire_all()
    assert snapshot(arqueo) == antes
    assert db_session.get(SesionCaja, nueva.id).estado == SesionCajaEstado.CERRADA
    assert db_session.get(SesionCaja, tercera.id).estado == SesionCajaEstado.ABIERTA
    assert db_session.scalars(select(SolicitudCorreccionArqueo)).all() == []


@pytest.mark.parametrize("operacion", ["cierre", "apertura"])
def test_fallo_transaccional_no_inventa_turno_y_permite_reintento(
    db_session: Session, monkeypatch, operacion,
):
    ctx = setup_cash_sales(db_session)
    service = CajaService(db_session)
    anterior = ctx["sesion"]
    caja_id, sesion_id = anterior.caja_id, anterior.id
    datos = CerrarSesionCajaRequest(usuario_id=100, efectivo_final_declarado=Decimal("10.00"))
    if operacion == "apertura":
        service.cerrar_sesion(sesion_id, datos)
    db_session.expire_all()
    historicos = [snapshot(row) for row in db_session.scalars(select(ArqueoCaja)).all()]
    sesion_antes = snapshot(anterior)
    db_session.rollback()

    def attempt():
        if operacion == "cierre":
            return service.cerrar_sesion(sesion_id, datos)
        return service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0"))

    def fail_commit():
        db_session.flush()  # Simula fallo tecnico despues de escribir y antes de confirmar.
        raise OperationalError("COMMIT", {}, RuntimeError("fallo tecnico simulado"))

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(OperationalError, match="fallo tecnico simulado"):
            attempt()
    db_session.expire_all()
    assert snapshot(db_session.get(SesionCaja, sesion_id)) == sesion_antes
    assert [snapshot(row) for row in db_session.scalars(select(ArqueoCaja)).all()] == historicos
    assert len(db_session.scalars(select(SesionCaja)).all()) == 2  # Incluye la otra caja del fixture.
    assert db_session.scalars(select(MovimientoCaja)).all() == []
    assert db_session.scalars(select(PagoVenta)).all() == []
    db_session.rollback()

    if operacion == "cierre":
        # El fallo no cuenta como cierre: impide otra apertura hasta recuperarse.
        with pytest.raises(ConflictError, match="sesion abierta"):
            service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0"))
        attempt()
        nueva = service.abrir_sesion(caja_id, cajero_id=101, efectivo_inicial=Decimal("0")).sesion
    else:
        # Una apertura revertida no vence el plazo de correccion.
        service.solicitar_correccion_arqueo(db_session.scalar(select(ArqueoCaja.id)), correction())
        nueva = attempt().sesion
    collect_sale(db_session, ctx, db_session.get(SesionCaja, nueva.id))
    assert service.get_sesion(nueva.id).estado == SesionCajaEstado.ABIERTA
