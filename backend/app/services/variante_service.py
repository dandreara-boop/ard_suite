from __future__ import annotations

from itertools import product

from sqlalchemy.orm import Session

from backend.app.business.catalog import (
    VariantCreationContext,
    VariantCreationPolicy,
    VariantValueInput,
)
from backend.app.models import Articulo, ValorAtributo, Variante, VarianteValorAtributo
from backend.app.repositories import ArticuloRepository, AtributoRepository, VarianteRepository
from backend.app.schemas import (
    VarianteCreate,
    VarianteGenerateCombination,
    VarianteGenerateRequest,
    VariantePreviewItemRead,
    VariantePreviewRequest,
    VariantePreviewValueRead,
    VarianteUpdate,
)
from backend.app.services.codigo_barra_service import CodigoBarraService
from backend.app.services.exceptions import (
    BusinessRuleViolation,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from backend.app.services.utils import update_model_from_schema


class VarianteService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = VarianteRepository(db)
        self.articulo_repository = ArticuloRepository(db)
        self.atributo_repository = AtributoRepository(db)
        self.codigo_barra_service = CodigoBarraService()
        self.creation_policy = VariantCreationPolicy()

    def list(self, skip: int = 0, limit: int = 100) -> list[Variante]:
        return self.repository.list(skip, limit)

    def get(self, variante_id: int) -> Variante:
        variante = self.repository.get_with_values(variante_id)
        if variante is None:
            raise NotFoundError("Variante no encontrada")
        return variante

    def get_by_codigo_barra(self, codigo_barra: str) -> Variante:
        variante = self.repository.get_by_codigo_barra(codigo_barra)
        if variante is None:
            raise NotFoundError("Variante no encontrada")
        return variante

    def create(self, data: VarianteCreate) -> Variante:
        articulo = self.articulo_repository.get_with_atributos(data.articulo_id)
        if articulo is None:
            raise NotFoundError("Articulo no encontrado")
        codigo_barra = self.codigo_barra_service.normalize_manual_code(data.codigo_barra)
        if not codigo_barra:
            raise ValidationError("El codigo de barras es obligatorio")

        valores = [self.atributo_repository.get_valor(valor_id) for valor_id in data.valor_atributo_ids]
        if any(valor is None for valor in valores):
            raise NotFoundError("Valor de atributo no encontrado")

        checked_values = [valor for valor in valores if valor is not None]
        self._ensure_variant_creation_allowed(articulo, checked_values, codigo_barra)

        variante_valores: list[VarianteValorAtributo] = []
        for valor in valores:
            assert valor is not None
            variante_valores.append(
                VarianteValorAtributo(
                    valor_atributo_id=valor.id,
                    atributo_id=valor.atributo_id,
                )
            )

        variante = Variante(
            articulo_id=data.articulo_id,
            codigo_barra=codigo_barra,
            activo=data.activo,
            valores=variante_valores,
        )
        self.repository.add(variante)
        self.db.commit()
        return self.get(variante.id)

    def update(self, variante_id: int, data: VarianteUpdate) -> Variante:
        variante = self.get(variante_id)
        if data.codigo_barra is not None:
            data.codigo_barra = self.codigo_barra_service.normalize_manual_code(data.codigo_barra)
            if not data.codigo_barra:
                raise ValidationError("El codigo de barras es obligatorio")
            existing = self.repository.get_by_codigo_barra(data.codigo_barra)
            if existing and existing.id != variante_id:
                raise ConflictError("Ya existe una variante con ese codigo de barras")
        update_model_from_schema(variante, data)
        self.db.commit()
        return self.get(variante_id)

    def set_active(self, variante_id: int, activo: bool) -> Variante:
        variante = self.get(variante_id)
        variante.activo = activo
        self.db.commit()
        return self.get(variante_id)

    def list_values(self, variante_id: int) -> list[VarianteValorAtributo]:
        self.get(variante_id)
        return self.repository.list_values(variante_id)

    def preview_for_articulo(
        self, articulo_id: int, data: VariantePreviewRequest
    ) -> list[VariantePreviewItemRead]:
        articulo = self._get_articulo_with_atributos(articulo_id)
        selections = self._validate_preview_selection(articulo, data)
        existing_combinations = self._existing_combinations(articulo_id)

        if not selections:
            combinations: list[tuple[ValorAtributo, ...]] = [()]
        else:
            combinations = list(product(*selections))

        preview_items: list[VariantePreviewItemRead] = []
        for combination in combinations:
            values = list(combination)
            value_ids = [value.id for value in values]
            proposed_barcode = self._generate_codigo_barra(articulo, values)
            exists_variant = frozenset(value_ids) in existing_combinations
            exists_barcode = self.repository.get_by_codigo_barra(proposed_barcode) is not None
            preview_items.append(
                VariantePreviewItemRead(
                    valores=[
                        VariantePreviewValueRead(
                            atributo_id=value.atributo_id,
                            atributo_nombre=value.atributo.nombre,
                            valor_atributo_id=value.id,
                            valor=value.valor,
                            codigo=value.codigo,
                        )
                        for value in values
                    ],
                    valor_atributo_ids=value_ids,
                    codigo_barra_propuesto=proposed_barcode,
                    existe_variante=exists_variant,
                    existe_codigo_barra=exists_barcode,
                    estado="EXISTENTE" if exists_variant else "NUEVA",
                )
            )

        return preview_items

    def generate_for_articulo(
        self, articulo_id: int, data: VarianteGenerateRequest
    ) -> list[Variante]:
        articulo = self._get_articulo_with_atributos(articulo_id)
        existing_combinations = self._existing_combinations(articulo_id)

        prepared: list[tuple[list[ValorAtributo], str]] = []
        seen_combinations: set[frozenset[int]] = set()
        seen_barcodes: set[str] = set()

        for combination in data.combinaciones:
            values = self._validate_generate_combination(articulo, combination)
            value_ids = frozenset(value.id for value in values)
            if value_ids in seen_combinations:
                raise ValidationError("La solicitud contiene una combinacion duplicada")

            codigo_barra = combination.codigo_barra
            if codigo_barra is None:
                codigo_barra = self._generate_codigo_barra(articulo, values)
            codigo_barra = self.codigo_barra_service.normalize_manual_code(codigo_barra)
            if not codigo_barra:
                raise ValidationError("El codigo de barras es obligatorio")
            if codigo_barra in seen_barcodes:
                raise ValidationError("La solicitud contiene un codigo de barras duplicado")

            self._ensure_variant_creation_allowed(
                articulo=articulo,
                valores=values,
                codigo_barra=codigo_barra,
                existing_combinations=existing_combinations | seen_combinations,
            )

            seen_combinations.add(value_ids)
            seen_barcodes.add(codigo_barra)
            prepared.append((values, codigo_barra))

        created_ids: list[int] = []
        try:
            for values, codigo_barra in prepared:
                variante = Variante(
                    articulo_id=articulo.id,
                    codigo_barra=codigo_barra,
                    activo=True,
                    valores=[
                        VarianteValorAtributo(
                            valor_atributo_id=value.id,
                            atributo_id=value.atributo_id,
                        )
                        for value in values
                    ],
                )
                self.db.add(variante)
                self.db.flush()
                created_ids.append(variante.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return [self.get(variante_id) for variante_id in created_ids]

    def _get_articulo_with_atributos(self, articulo_id: int) -> Articulo:
        articulo = self.articulo_repository.get_with_atributos(articulo_id)
        if articulo is None:
            raise NotFoundError("Articulo no encontrado")
        return articulo

    def _allowed_attribute_order(self, articulo: Articulo) -> dict[int, int]:
        return {
            item.atributo_id: item.orden
            for item in sorted(articulo.atributos, key=lambda item: (item.orden, item.id))
        }

    def _validate_preview_selection(
        self, articulo: Articulo, data: VariantePreviewRequest
    ) -> list[list[ValorAtributo]]:
        allowed_order = self._allowed_attribute_order(articulo)
        seen_attribute_ids: set[int] = set()
        selections_by_attribute: dict[int, list[ValorAtributo]] = {}

        for selection in data.atributos:
            if selection.atributo_id in seen_attribute_ids:
                raise ValidationError("La solicitud contiene un atributo duplicado")
            if selection.atributo_id not in allowed_order:
                raise ValidationError("El atributo no esta habilitado para el articulo")
            seen_attribute_ids.add(selection.atributo_id)
            values = [self.atributo_repository.get_valor(value_id) for value_id in selection.valor_atributo_ids]
            if any(value is None for value in values):
                raise NotFoundError("Valor de atributo no encontrado")
            checked_values: list[ValorAtributo] = []
            seen_value_ids: set[int] = set()
            for value in values:
                assert value is not None
                if value.atributo_id != selection.atributo_id:
                    raise ValidationError("El valor no corresponde al atributo indicado")
                if value.id in seen_value_ids:
                    raise ValidationError("La solicitud contiene un valor duplicado")
                seen_value_ids.add(value.id)
                checked_values.append(value)
            if checked_values:
                selections_by_attribute[selection.atributo_id] = checked_values

        return [
            selections_by_attribute[attribute_id]
            for attribute_id in sorted(selections_by_attribute, key=lambda attr_id: allowed_order[attr_id])
        ]

    def _validate_generate_combination(
        self, articulo: Articulo, combination: VarianteGenerateCombination
    ) -> list[ValorAtributo]:
        allowed_order = self._allowed_attribute_order(articulo)
        values = [self.atributo_repository.get_valor(value_id) for value_id in combination.valor_atributo_ids]
        if any(value is None for value in values):
            raise NotFoundError("Valor de atributo no encontrado")

        checked_values = [value for value in values if value is not None]
        return sorted(
            checked_values,
            key=lambda value: (
                allowed_order.get(value.atributo_id, len(allowed_order)),
                value.atributo_id,
                value.id,
            ),
        )

    def _ensure_variant_creation_allowed(
        self,
        articulo: Articulo,
        valores: list[ValorAtributo],
        codigo_barra: str,
        existing_combinations: set[frozenset[int]] | None = None,
    ) -> None:
        existing_barcode_variant = self.repository.get_by_codigo_barra(codigo_barra)
        context = VariantCreationContext(
            articulo_id=articulo.id,
            valores=[
                VariantValueInput(
                    valor_atributo_id=valor.id,
                    atributo_id=valor.atributo_id,
                )
                for valor in valores
            ],
            allowed_attribute_ids={item.atributo_id for item in articulo.atributos},
            existing_variant_value_ids=existing_combinations
            if existing_combinations is not None
            else self._existing_combinations(articulo.id),
            codigo_barra=codigo_barra,
            codigo_barra_existing_variant_id=existing_barcode_variant.id
            if existing_barcode_variant is not None
            else None,
        )
        blocking_result = self.creation_policy.first_blocking_result(context)
        if blocking_result is not None:
            raise BusinessRuleViolation(
                code=blocking_result.code,
                message=blocking_result.message,
                status=blocking_result.status,
                data=blocking_result.data,
            )

    def _generate_codigo_barra(self, articulo: Articulo, values: list[ValorAtributo]) -> str:
        codigos_valores: list[str] = []
        for value in values:
            if not value.codigo:
                raise ValidationError("Todos los valores seleccionados deben tener codigo")
            codigos_valores.append(value.codigo)
        return self.codigo_barra_service.generate_for_variant(articulo.codigo, codigos_valores)

    def _existing_combinations(self, articulo_id: int) -> set[frozenset[int]]:
        combinations: set[frozenset[int]] = set()
        for variante in self.repository.list_by_articulo_with_values(articulo_id):
            combinations.add(frozenset(item.valor_atributo_id for item in variante.valores))
        return combinations
