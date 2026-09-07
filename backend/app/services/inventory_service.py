from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.business.inventory import (
    INVENTORY_EVENT_ALREADY_PROCESSED,
    INVENTORY_MOVEMENT_ALLOWED,
    InventoryMovementContext,
    InventoryMovementPolicy,
)
from backend.app.business.rules import RuleResult
from backend.app.events import DomainEvent
from backend.app.models import (
    DestinoInventario,
    MovimientoStock,
    MovimientoStockTipo,
    StockActual,
    StockEstado,
    Variante,
)
from backend.app.repositories.inventory_repository import (
    DestinoInventarioRepository,
    MovimientoStockRepository,
    StockActualRepository,
)
from backend.app.schemas.inventory import AjusteStockCreate, DestinoInventarioCreate, MovimientoStockCreate
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, ValidationError


@dataclass(frozen=True)
class InventoryMovementOutcome:
    applied: bool
    rule_result: RuleResult
    movimiento: MovimientoStock
    stock: StockActual | None


class InventoryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.destinos = DestinoInventarioRepository(db)
        self.stock = StockActualRepository(db)
        self.movimientos = MovimientoStockRepository(db)
        self.policy = InventoryMovementPolicy()

    def create_destino(self, data: DestinoInventarioCreate) -> DestinoInventario:
        if self.destinos.get_by_codigo(data.codigo):
            raise ConflictError("Ya existe un destino de inventario con ese codigo")
        destino = DestinoInventario(**data.model_dump())
        self.destinos.add(destino)
        self.db.commit()
        return destino

    def list_destinos(self) -> list[DestinoInventario]:
        return self.destinos.list(limit=1000)

    def registrar_movimiento(self, data: MovimientoStockCreate) -> InventoryMovementOutcome:
        existing = self.movimientos.get_by_global_id(data.global_id)
        if existing is not None:
            result = RuleResult.denied(
                INVENTORY_EVENT_ALREADY_PROCESSED,
                "El evento de inventario ya fue procesado.",
                {"global_id": data.global_id},
            )
            stock = self.stock.get_by_key(existing.variante_id, existing.destino_id, existing.estado)
            return InventoryMovementOutcome(False, result, existing, stock)

        variante = self.db.get(Variante, data.variante_id)
        destino = self.destinos.get(data.destino_id)
        context = InventoryMovementContext(
            variante_exists=variante is not None,
            variante_active=bool(variante and variante.activo),
            destino_exists=destino is not None,
            destino_active=bool(destino and destino.activo),
            cantidad=data.cantidad,
            duplicate_global_id=False,
        )
        blocking_result = self.policy.first_blocking_result(context)
        if blocking_result is not None:
            raise BusinessRuleViolation(
                blocking_result.code,
                blocking_result.message,
                blocking_result.status,
                blocking_result.data,
            )

        try:
            saldo_anterior = self._saldo_actual(data.variante_id, data.destino_id, data.estado)
            movimiento = MovimientoStock(
                global_id=data.global_id,
                variante_id=data.variante_id,
                destino_id=data.destino_id,
                estado=data.estado,
                cantidad=data.cantidad,
                tipo=data.tipo,
                fecha=data.fecha,
                usuario_id=data.usuario_id,
                origen_tipo=data.origen_tipo,
                origen_id=data.origen_id,
                referencia=data.referencia,
                motivo=data.motivo,
                observacion=data.observacion,
                saldo_anterior=saldo_anterior,
            )
            if movimiento.fecha is None:
                movimiento.fecha = datetime.now()
            self.db.add(movimiento)
            self.db.flush()
            stock = self._apply_stock_delta(data.variante_id, data.destino_id, data.estado, data.cantidad)
            movimiento.saldo_resultante = stock.cantidad
            self.db.commit()
            self.db.refresh(movimiento)
            self.db.refresh(stock)
        except Exception:
            self.db.rollback()
            raise

        return InventoryMovementOutcome(
            True,
            RuleResult.allowed(INVENTORY_MOVEMENT_ALLOWED, "El movimiento fue registrado."),
            movimiento,
            stock,
        )

    def ajustar_stock(self, data: AjusteStockCreate) -> InventoryMovementOutcome:
        diferencia = data.stock_contado - data.stock_teorico
        tipo = (
            MovimientoStockTipo.AJUSTE_POSITIVO
            if diferencia > 0
            else MovimientoStockTipo.AJUSTE_NEGATIVO
        )
        movimiento = MovimientoStockCreate(
            global_id=data.global_id,
            variante_id=data.variante_id,
            destino_id=data.destino_id,
            estado=data.estado,
            cantidad=diferencia,
            tipo=tipo,
            usuario_id=data.usuario_id,
            origen_tipo="AJUSTE_STOCK",
            referencia=data.referencia,
            motivo=data.motivo,
            observacion=self._build_adjustment_observation(data),
        )
        return self.registrar_movimiento(movimiento)

    def list_stock(
        self,
        *,
        variante_id: int | None = None,
        destino_id: int | None = None,
        estado: StockEstado | None = None,
    ) -> list[StockActual]:
        return self.stock.list_filtered(variante_id=variante_id, destino_id=destino_id, estado=estado)

    def list_movimientos(
        self,
        *,
        variante_id: int | None = None,
        destino_id: int | None = None,
        tipo: MovimientoStockTipo | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
    ) -> list[MovimientoStock]:
        return self.movimientos.list_filtered(
            variante_id=variante_id,
            destino_id=destino_id,
            tipo=tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

    def movement_from_domain_event(self, event: DomainEvent) -> MovimientoStockCreate:
        if event.event_type != "InventoryMovementRequested":
            raise ValidationError("El evento de dominio no solicita un movimiento de inventario")
        return MovimientoStockCreate(global_id=event.event_id, **event.payload)

    def rebuild_stock_actual(self) -> int:
        rows = self.db.execute(
            select(
                MovimientoStock.variante_id,
                MovimientoStock.destino_id,
                MovimientoStock.estado,
                func.sum(MovimientoStock.cantidad),
            ).group_by(
                MovimientoStock.variante_id,
                MovimientoStock.destino_id,
                MovimientoStock.estado,
            )
        ).all()
        try:
            self.db.query(StockActual).delete()
            rebuilt = 0
            for variante_id, destino_id, estado, cantidad in rows:
                self.db.add(
                    StockActual(
                        variante_id=variante_id,
                        destino_id=destino_id,
                        estado=estado,
                        cantidad=int(cantidad or 0),
                    )
                )
                rebuilt += 1
            self.db.commit()
            return rebuilt
        except Exception:
            self.db.rollback()
            raise

    def _saldo_actual(self, variante_id: int, destino_id: int, estado: StockEstado) -> int:
        current = self.stock.get_by_key(variante_id, destino_id, estado)
        return current.cantidad if current is not None else 0

    def _apply_stock_delta(
        self, variante_id: int, destino_id: int, estado: StockEstado, cantidad: int
    ) -> StockActual:
        bind = self.db.get_bind()
        dialect = bind.dialect.name
        values = {
            "variante_id": variante_id,
            "destino_id": destino_id,
            "estado": estado,
            "cantidad": cantidad,
        }
        if dialect == "mysql":
            statement = mysql_insert(StockActual).values(**values)
            statement = statement.on_duplicate_key_update(
                cantidad=StockActual.cantidad + cantidad,
                updated_at=func.now(),
            )
            self.db.execute(statement)
        elif dialect == "sqlite":
            statement = sqlite_insert(StockActual).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["variante_id", "destino_id", "estado"],
                set_={
                    "cantidad": StockActual.cantidad + cantidad,
                    "updated_at": func.now(),
                },
            )
            self.db.execute(statement)
        else:
            current = self.stock.get_by_key(variante_id, destino_id, estado, for_update=True)
            if current is None:
                current = StockActual(
                    variante_id=variante_id,
                    destino_id=destino_id,
                    estado=estado,
                    cantidad=cantidad,
                )
                self.db.add(current)
            else:
                current.cantidad += cantidad
            self.db.flush()
        self.db.flush()
        stock = self.stock.get_by_key(variante_id, destino_id, estado)
        if stock is None:
            raise RuntimeError("No se pudo materializar StockActual")
        return stock

    def _build_adjustment_observation(self, data: AjusteStockCreate) -> str:
        parts = [
            f"motivo={data.motivo}",
            f"saldo_anterior={data.stock_teorico}",
            f"saldo_resultante={data.stock_contado}",
        ]
        if data.observacion:
            parts.append(f"observacion={data.observacion}")
        return "; ".join(parts)
