from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from backend.app.business.pricing import (
    ARTICLE_PRICE_INVALID,
    ARTICLE_PRICE_NOT_FOUND,
    PRICE_BASE_REQUIRED,
    PRICE_CIRCULAR_DEPENDENCY,
    PRICE_CONDITION_INACTIVE,
    PRICE_CONDITION_NOT_FOUND,
    PRICE_MANUAL_OVERRIDE_INVALID,
    PRICE_PERCENTAGE_INVALID,
    PRICE_RECALCULATION_POLICY_REQUIRED,
    PRICE_ROUNDING_INVALID,
    PRICE_RULE_INVALID,
    PRICE_RULE_REQUIRED,
    PriceRuleEngine,
)
from backend.app.business.rules import RuleStatus
from backend.app.models import Articulo
from backend.app.models.pricing import (
    AuditoriaPrecioArticulo,
    CondicionComercialPrecio,
    CondicionPrecioTipo,
    MotivoAuditoriaPrecio,
    OrigenPrecioArticulo,
    PrecioArticulo,
    TipoRedondeoPrecio,
)
from backend.app.repositories.pricing_repository import (
    AuditoriaPrecioArticuloRepository,
    CondicionComercialPrecioRepository,
    PrecioArticuloRepository,
)
from backend.app.schemas.pricing import (
    AplicarRecalculoReglaRead,
    AplicarRecalculoReglaRequest,
    CondicionComercialPrecioCreate,
    CondicionComercialPrecioUpdate,
    PoliticaRecalculoManual,
    PrecioBaseArticuloRequest,
    PrecioManualArticuloRequest,
    RecalculoReglaItemRead,
    RecalculoReglaPreviewRead,
    RecalculoReglaPreviewRequest,
)
from backend.app.services.exceptions import BusinessRuleViolation, ConflictError, NotFoundError, ValidationError

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class _DerivedPrice:
    condicion: CondicionComercialPrecio
    precio: Decimal


class PricingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.condiciones = CondicionComercialPrecioRepository(db)
        self.precios = PrecioArticuloRepository(db)
        self.auditoria = AuditoriaPrecioArticuloRepository(db)
        self.engine = PriceRuleEngine()

    def list_condiciones(self, active_only: bool = False) -> list[CondicionComercialPrecio]:
        return self.condiciones.list_ordered(active_only=active_only)

    def get_condicion(self, condicion_id: int) -> CondicionComercialPrecio:
        condicion = self.condiciones.get(condicion_id)
        if condicion is None:
            self._raise(PRICE_CONDITION_NOT_FOUND, "Condicion comercial de precio no encontrada.")
        return condicion

    def create_condicion(self, data: CondicionComercialPrecioCreate) -> CondicionComercialPrecio:
        if self.condiciones.get_by_codigo(data.codigo):
            raise ConflictError("Ya existe una condicion comercial con ese codigo")
        condicion = CondicionComercialPrecio(**data.model_dump())
        try:
            self._validate_condicion(condicion)
            self.condiciones.add(condicion)
            self._ensure_no_cycles(condicion)
            self.db.commit()
            self.db.refresh(condicion)
            return condicion
        except Exception:
            self.db.rollback()
            raise

    def update_condicion(
        self,
        condicion_id: int,
        data: CondicionComercialPrecioUpdate,
    ) -> CondicionComercialPrecio:
        condicion = self.get_condicion(condicion_id)
        if data.codigo is not None:
            existing = self.condiciones.get_by_codigo(data.codigo)
            if existing is not None and existing.id != condicion.id:
                raise ConflictError("Ya existe una condicion comercial con ese codigo")
        try:
            payload = data.model_dump(exclude_unset=True)
            for field, value in payload.items():
                setattr(condicion, field, value)
            self._validate_condicion(condicion)
            self._ensure_no_cycles(condicion)
            self.db.commit()
            self.db.refresh(condicion)
            return condicion
        except Exception:
            self.db.rollback()
            raise

    def list_precios_articulo(self, articulo_id: int) -> list[PrecioArticulo]:
        self._ensure_articulo(articulo_id)
        return self.precios.list_by_articulo(articulo_id)

    def set_precio_base(self, articulo_id: int, data: PrecioBaseArticuloRequest) -> list[PrecioArticulo]:
        self._ensure_articulo(articulo_id)
        condicion_base = self._get_base_condition(data.condicion_comercial_id)
        if condicion_base.tipo != CondicionPrecioTipo.BASE:
            self._raise(PRICE_BASE_REQUIRED, "La condicion indicada no es BASE.")
        if not condicion_base.activa:
            self._raise(PRICE_CONDITION_INACTIVE, "La condicion BASE esta inactiva.")
        precio_base = self._money(data.precio)
        if precio_base <= 0:
            self._raise(ARTICLE_PRICE_INVALID, "El precio base debe ser mayor a cero.")
        try:
            base_price = self._upsert_price(
                articulo_id=articulo_id,
                condicion_id=condicion_base.id,
                precio=precio_base,
                origen=OrigenPrecioArticulo.REGLA,
                motivo=MotivoAuditoriaPrecio.CAMBIO_BASE,
                usuario_id=data.usuario_id,
            )
            self.db.flush()
            derived = self._calculate_all_derived({condicion_base.id: base_price.precio})
            for item in derived:
                self._upsert_price(
                    articulo_id=articulo_id,
                    condicion_id=item.condicion.id,
                    precio=item.precio,
                    origen=OrigenPrecioArticulo.REGLA,
                    motivo=MotivoAuditoriaPrecio.RECALCULO_REGLA,
                    usuario_id=data.usuario_id,
                )
            self.db.commit()
            return self.list_precios_articulo(articulo_id)
        except Exception:
            self.db.rollback()
            raise

    def set_precio_manual(self, articulo_id: int, data: PrecioManualArticuloRequest) -> PrecioArticulo:
        self._ensure_articulo(articulo_id)
        condicion = self.get_condicion(data.condicion_comercial_id)
        if not condicion.activa:
            self._raise(PRICE_CONDITION_INACTIVE, "La condicion comercial esta inactiva.")
        if condicion.tipo != CondicionPrecioTipo.DERIVADA:
            self._raise(PRICE_MANUAL_OVERRIDE_INVALID, "Solo se permite modificar manualmente precios derivados.")
        precio = self._money(data.precio)
        if precio <= 0:
            self._raise(ARTICLE_PRICE_INVALID, "El precio manual debe ser mayor a cero.")
        try:
            price = self._upsert_price(
                articulo_id=articulo_id,
                condicion_id=condicion.id,
                precio=precio,
                origen=OrigenPrecioArticulo.MANUAL,
                motivo=MotivoAuditoriaPrecio.MODIFICACION_MANUAL,
                usuario_id=data.usuario_id,
            )
            self.db.commit()
            self.db.refresh(price)
            return price
        except Exception:
            self.db.rollback()
            raise

    def get_precio_vigente(self, articulo_id: int, condicion_id: int) -> PrecioArticulo:
        price = self.precios.get_by_articulo_condicion(articulo_id, condicion_id)
        if price is None:
            self._raise(ARTICLE_PRICE_NOT_FOUND, "Precio de articulo no encontrado.")
        if not price.condicion_comercial.activa:
            self._raise(PRICE_CONDITION_INACTIVE, "La condicion comercial esta inactiva.")
        return price

    def preview_recalculo_regla(
        self,
        condicion_id: int,
        data: RecalculoReglaPreviewRequest,
    ) -> RecalculoReglaPreviewRead:
        condicion = self.get_condicion(condicion_id)
        proposed = self._condition_with_rule(condition=condicion, data=data)
        self._validate_condicion(proposed, require_unique_base=False)
        items = self._build_recalculo_items(proposed)
        return RecalculoReglaPreviewRead(
            articulos_afectados=len(items),
            precios_regla=sum(1 for item in items if item.origen_actual == OrigenPrecioArticulo.REGLA),
            precios_manual=sum(1 for item in items if item.origen_actual == OrigenPrecioArticulo.MANUAL),
            items=items,
        )

    def aplicar_recalculo_regla(
        self,
        condicion_id: int,
        data: AplicarRecalculoReglaRequest,
    ) -> AplicarRecalculoReglaRead:
        if not data.confirmar:
            self._raise(PRICE_RECALCULATION_POLICY_REQUIRED, "El mantenimiento requiere confirmacion explicita.")
        if data.politica_manuales == PoliticaRecalculoManual.REVISAR_EXCEPCIONES:
            self._raise(
                PRICE_RECALCULATION_POLICY_REQUIRED,
                "REVISAR_EXCEPCIONES queda reservado para seleccion explicita futura.",
            )
        condicion = self.get_condicion(condicion_id)
        try:
            condicion.tipo_regla = data.tipo_regla
            condicion.porcentaje = data.porcentaje
            condicion.tipo_redondeo = data.tipo_redondeo
            condicion.multiplo_redondeo = data.multiplo_redondeo
            self._validate_condicion(condicion)
            self._ensure_no_cycles(condicion)
            items = self._build_recalculo_items(condicion)
            updated = 0
            conserved = 0
            for item in items:
                price = self.precios.get_by_articulo_condicion(item.articulo_id, item.condicion_comercial_id)
                if price is None:
                    continue
                if (
                    item.origen_actual == OrigenPrecioArticulo.MANUAL
                    and data.politica_manuales == PoliticaRecalculoManual.CONSERVAR_MANUALES
                ):
                    conserved += 1
                    continue
                changed = self._change_price(
                    price=price,
                    precio=item.precio_propuesto,
                    origen=OrigenPrecioArticulo.REGLA,
                    motivo=MotivoAuditoriaPrecio.CAMBIO_REGLA_MASIVO,
                    usuario_id=data.usuario_id,
                )
                if changed:
                    updated += 1
            self.db.commit()
            return AplicarRecalculoReglaRead(
                condicion_comercial_id=condicion.id,
                articulos_afectados=len(items),
                precios_actualizados=updated,
                precios_manual_conservados=conserved,
            )
        except Exception:
            self.db.rollback()
            raise

    def _validate_condicion(
        self,
        condicion: CondicionComercialPrecio,
        *,
        require_unique_base: bool = True,
    ) -> None:
        if condicion.tipo == CondicionPrecioTipo.BASE:
            if condicion.condicion_base_id is not None or condicion.tipo_regla is not None or condicion.porcentaje is not None:
                self._raise(PRICE_RULE_INVALID, "Una condicion BASE no debe tener regla ni condicion base.")
            if require_unique_base and condicion.activa:
                active_base = self.condiciones.get_active_base()
                if active_base is not None and active_base.id != condicion.id:
                    self._raise(PRICE_RULE_INVALID, "Ya existe una condicion BASE activa.")
        elif condicion.tipo == CondicionPrecioTipo.DERIVADA:
            if condicion.condicion_base_id is None:
                self._raise(PRICE_BASE_REQUIRED, "Una condicion DERIVADA requiere condicion base.")
            if condicion.id is not None and condicion.condicion_base_id == condicion.id:
                self._raise(PRICE_CIRCULAR_DEPENDENCY, "Una condicion no puede depender de si misma.")
            if condicion.tipo_regla is None:
                self._raise(PRICE_RULE_REQUIRED, "Una condicion DERIVADA requiere regla.")
            if condicion.porcentaje is None or Decimal(condicion.porcentaje) < 0:
                self._raise(PRICE_PERCENTAGE_INVALID, "El porcentaje debe ser mayor o igual a cero.")
            base = self.condiciones.get(condicion.condicion_base_id)
            if base is None:
                self._raise(PRICE_BASE_REQUIRED, "La condicion base no existe.")
        if condicion.tipo_redondeo == TipoRedondeoPrecio.MULTIPLO:
            if condicion.multiplo_redondeo is None or Decimal(condicion.multiplo_redondeo) <= 0:
                self._raise(PRICE_ROUNDING_INVALID, "El redondeo MULTIPLO requiere un multiplo mayor a cero.")
        elif condicion.multiplo_redondeo is not None:
            self._raise(PRICE_ROUNDING_INVALID, "Solo MULTIPLO permite multiplo_redondeo.")

    def _ensure_no_cycles(self, condicion: CondicionComercialPrecio) -> None:
        seen = {condicion.id} if condicion.id is not None else set()
        next_id = condicion.condicion_base_id
        while next_id is not None:
            if next_id in seen:
                self._raise(PRICE_CIRCULAR_DEPENDENCY, "Dependencia circular entre condiciones de precio.")
            seen.add(next_id)
            parent = self.condiciones.get(next_id)
            next_id = parent.condicion_base_id if parent is not None else None

    def _calculate_all_derived(self, known_prices: dict[int, Decimal]) -> list[_DerivedPrice]:
        remaining = self.condiciones.list_active_derived()
        calculated: list[_DerivedPrice] = []
        while remaining:
            progressed = False
            next_remaining: list[CondicionComercialPrecio] = []
            for condicion in remaining:
                base_id = condicion.condicion_base_id
                if base_id in known_prices:
                    price = self._calculate_condition_price(condicion, known_prices[base_id])
                    known_prices[condicion.id] = price
                    calculated.append(_DerivedPrice(condicion=condicion, precio=price))
                    progressed = True
                else:
                    next_remaining.append(condicion)
            if not progressed and next_remaining:
                self._raise(PRICE_CIRCULAR_DEPENDENCY, "No se pudieron resolver dependencias de precios.")
            remaining = next_remaining
        return calculated

    def _calculate_condition_price(self, condicion: CondicionComercialPrecio, base_price: Decimal) -> Decimal:
        if condicion.tipo_regla is None or condicion.porcentaje is None:
            self._raise(PRICE_RULE_REQUIRED, "La condicion derivada no tiene regla completa.")
        return self.engine.calculate(
            precio_base=base_price,
            tipo_regla=condicion.tipo_regla,
            porcentaje=condicion.porcentaje,
            tipo_redondeo=condicion.tipo_redondeo,
            multiplo_redondeo=condicion.multiplo_redondeo,
        )

    def _build_recalculo_items(self, condicion: CondicionComercialPrecio) -> list[RecalculoReglaItemRead]:
        if condicion.tipo != CondicionPrecioTipo.DERIVADA or condicion.condicion_base_id is None:
            self._raise(PRICE_RULE_REQUIRED, "El recalculo masivo aplica sobre condiciones DERIVADAS.")
        items: list[RecalculoReglaItemRead] = []
        for current in self.precios.list_with_articles_for_condition(condicion.id):
            base = self.precios.get_by_articulo_condicion(current.articulo_id, condicion.condicion_base_id)
            if base is None:
                continue
            proposed = self._calculate_condition_price(condicion, base.precio)
            items.append(
                RecalculoReglaItemRead(
                    articulo_id=current.articulo_id,
                    articulo_codigo=current.articulo.codigo,
                    articulo_nombre=current.articulo.nombre,
                    condicion_comercial_id=condicion.id,
                    precio_actual=current.precio,
                    precio_propuesto=proposed,
                    origen_actual=current.origen,
                )
            )
        return items

    def _condition_with_rule(
        self,
        *,
        condition: CondicionComercialPrecio,
        data: RecalculoReglaPreviewRequest,
    ) -> CondicionComercialPrecio:
        return CondicionComercialPrecio(
            id=condition.id,
            codigo=condition.codigo,
            nombre=condition.nombre,
            tipo=condition.tipo,
            condicion_base_id=condition.condicion_base_id,
            tipo_regla=data.tipo_regla,
            porcentaje=data.porcentaje,
            tipo_redondeo=data.tipo_redondeo,
            multiplo_redondeo=data.multiplo_redondeo,
            activa=condition.activa,
            orden=condition.orden,
        )

    def _upsert_price(
        self,
        *,
        articulo_id: int,
        condicion_id: int,
        precio: Decimal,
        origen: OrigenPrecioArticulo,
        motivo: MotivoAuditoriaPrecio,
        usuario_id: int | None,
    ) -> PrecioArticulo:
        price = self.precios.get_by_articulo_condicion(articulo_id, condicion_id)
        if price is None:
            price = PrecioArticulo(
                articulo_id=articulo_id,
                condicion_comercial_id=condicion_id,
                precio=precio,
                origen=origen,
            )
            self.db.add(price)
            self.db.flush()
            self._audit(
                articulo_id=articulo_id,
                condicion_id=condicion_id,
                precio_anterior=None,
                precio_nuevo=precio,
                origen_anterior=None,
                origen_nuevo=origen,
                motivo=MotivoAuditoriaPrecio.CREACION if motivo == MotivoAuditoriaPrecio.CAMBIO_BASE else motivo,
                usuario_id=usuario_id,
            )
            return price
        self._change_price(
            price=price,
            precio=precio,
            origen=origen,
            motivo=motivo,
            usuario_id=usuario_id,
        )
        return price

    def _change_price(
        self,
        *,
        price: PrecioArticulo,
        precio: Decimal,
        origen: OrigenPrecioArticulo,
        motivo: MotivoAuditoriaPrecio,
        usuario_id: int | None,
    ) -> bool:
        old_price = price.precio
        old_origin = price.origen
        if old_price == precio and old_origin == origen:
            return False
        price.precio = precio
        price.origen = origen
        self.db.flush()
        self._audit(
            articulo_id=price.articulo_id,
            condicion_id=price.condicion_comercial_id,
            precio_anterior=old_price,
            precio_nuevo=precio,
            origen_anterior=old_origin,
            origen_nuevo=origen,
            motivo=motivo,
            usuario_id=usuario_id,
        )
        return True

    def _audit(
        self,
        *,
        articulo_id: int,
        condicion_id: int,
        precio_anterior: Decimal | None,
        precio_nuevo: Decimal,
        origen_anterior: OrigenPrecioArticulo | None,
        origen_nuevo: OrigenPrecioArticulo,
        motivo: MotivoAuditoriaPrecio,
        usuario_id: int | None,
    ) -> None:
        self.db.add(
            AuditoriaPrecioArticulo(
                articulo_id=articulo_id,
                condicion_comercial_id=condicion_id,
                precio_anterior=precio_anterior,
                precio_nuevo=precio_nuevo,
                origen_anterior=origen_anterior,
                origen_nuevo=origen_nuevo,
                motivo=motivo,
                usuario_id=usuario_id,
            )
        )

    def _get_base_condition(self, condicion_id: int | None) -> CondicionComercialPrecio:
        if condicion_id is not None:
            return self.get_condicion(condicion_id)
        base = self.condiciones.get_active_base()
        if base is None:
            self._raise(PRICE_BASE_REQUIRED, "No existe una condicion BASE activa.")
        return base

    def _ensure_articulo(self, articulo_id: int) -> Articulo:
        articulo = self.db.get(Articulo, articulo_id)
        if articulo is None:
            raise NotFoundError("Articulo no encontrado")
        return articulo

    def _money(self, value: Decimal) -> Decimal:
        return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)

    def _raise(self, code: str, message: str) -> None:
        raise BusinessRuleViolation(code, message, RuleStatus.DENIED)
