from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.business.catalog.variant_rules import (
    BARCODE_ALREADY_EXISTS,
    VARIANT_ALREADY_EXISTS,
    VARIANT_ATTRIBUTES_NOT_ALLOWED,
    VARIANT_CAN_BE_CREATED,
    VARIANT_DUPLICATE_ATTRIBUTE,
    VariantAttributesAllowedRule,
    VariantBarcodeUniqueRule,
    VariantCreationContext,
    VariantCreationPolicy,
    VariantDuplicateCombinationRule,
    VariantOneValuePerAttributeRule,
    VariantValueInput,
)
from backend.app.business.rules import RuleStatus
from backend.app.db.base import Base
from backend.app.models import Articulo, ArticuloAtributo, Atributo, Categoria, ValorAtributo
from backend.app.schemas import VarianteCreate
from backend.app.services.exceptions import BusinessRuleViolation
from backend.app.services.variante_service import VarianteService


def context(
    *,
    valores: list[VariantValueInput] | None = None,
    allowed_attribute_ids: set[int] | None = None,
    existing_variant_value_ids: set[frozenset[int]] | None = None,
    codigo_barra_existing_variant_id: int | None = None,
) -> VariantCreationContext:
    return VariantCreationContext(
        articulo_id=1,
        valores=valores
        if valores is not None
        else [
            VariantValueInput(valor_atributo_id=10, atributo_id=1),
            VariantValueInput(valor_atributo_id=20, atributo_id=2),
        ],
        allowed_attribute_ids=allowed_attribute_ids if allowed_attribute_ids is not None else {1, 2},
        existing_variant_value_ids=existing_variant_value_ids or set(),
        codigo_barra="1234560101",
        codigo_barra_existing_variant_id=codigo_barra_existing_variant_id,
    )


def test_rule_attributes_allowed() -> None:
    result = VariantAttributesAllowedRule().evaluate(context())

    assert result.status == RuleStatus.ALLOWED


def test_rule_attribute_not_allowed() -> None:
    result = VariantAttributesAllowedRule().evaluate(
        context(
            valores=[VariantValueInput(valor_atributo_id=10, atributo_id=1)],
            allowed_attribute_ids={2},
        )
    )

    assert result.status == RuleStatus.DENIED
    assert result.code == VARIANT_ATTRIBUTES_NOT_ALLOWED
    assert result.data == {"atributo_ids": [1]}


def test_rule_duplicate_attribute() -> None:
    result = VariantOneValuePerAttributeRule().evaluate(
        context(
            valores=[
                VariantValueInput(valor_atributo_id=10, atributo_id=1),
                VariantValueInput(valor_atributo_id=11, atributo_id=1),
            ]
        )
    )

    assert result.status == RuleStatus.DENIED
    assert result.code == VARIANT_DUPLICATE_ATTRIBUTE


def test_rule_existing_variant_combination() -> None:
    result = VariantDuplicateCombinationRule().evaluate(
        context(existing_variant_value_ids={frozenset({10, 20})})
    )

    assert result.status == RuleStatus.DENIED
    assert result.code == VARIANT_ALREADY_EXISTS


def test_rule_barcode_already_used() -> None:
    result = VariantBarcodeUniqueRule().evaluate(context(codigo_barra_existing_variant_id=7))

    assert result.status == RuleStatus.DENIED
    assert result.code == BARCODE_ALREADY_EXISTS
    assert result.data["variante_id"] == 7


def test_policy_valid_combination_allowed() -> None:
    results = VariantCreationPolicy().evaluate(context())

    assert results[-1].status == RuleStatus.ALLOWED
    assert results[-1].code == VARIANT_CAN_BE_CREATED


def test_policy_collects_relevant_validation_errors() -> None:
    results = VariantCreationPolicy().evaluate(
        context(
            valores=[
                VariantValueInput(valor_atributo_id=10, atributo_id=1),
                VariantValueInput(valor_atributo_id=11, atributo_id=1),
            ],
            allowed_attribute_ids={2},
        )
    )

    denied_codes = {result.code for result in results if result.status == RuleStatus.DENIED}
    assert denied_codes == {VARIANT_ATTRIBUTES_NOT_ALLOWED, VARIANT_DUPLICATE_ATTRIBUTE}


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


def seed_variant_catalog(db: Session) -> dict[str, int]:
    categoria = Categoria(nombre="Remeras")
    talle = Atributo(nombre="Talle", codigo="TAL")
    color = Atributo(nombre="Color", codigo="COL")
    db.add_all([categoria, talle, color])
    db.flush()

    talle_1 = ValorAtributo(atributo_id=talle.id, valor="1", codigo="01")
    talle_2 = ValorAtributo(atributo_id=talle.id, valor="2", codigo="02")
    blanco = ValorAtributo(atributo_id=color.id, valor="Blanco", codigo="01")
    articulo = Articulo(codigo="123456", nombre="Remera lisa", categoria_id=categoria.id)
    db.add_all([talle_1, talle_2, blanco, articulo])
    db.flush()
    db.add_all(
        [
            ArticuloAtributo(articulo_id=articulo.id, atributo_id=talle.id, orden=1),
            ArticuloAtributo(articulo_id=articulo.id, atributo_id=color.id, orden=2),
        ]
    )
    db.commit()
    return {
        "articulo_id": articulo.id,
        "talle_1_id": talle_1.id,
        "talle_2_id": talle_2.id,
        "blanco_id": blanco.id,
    }


def test_variante_service_uses_policy_and_does_not_persist_denied_variant(
    db_session: Session,
) -> None:
    ids = seed_variant_catalog(db_session)
    service = VarianteService(db_session)
    service.create(
        VarianteCreate(
            articulo_id=ids["articulo_id"],
            codigo_barra="1234560101",
            valor_atributo_ids=[ids["talle_1_id"], ids["blanco_id"]],
        )
    )

    with pytest.raises(BusinessRuleViolation) as error:
        service.create(
            VarianteCreate(
                articulo_id=ids["articulo_id"],
                codigo_barra="1234560201",
                valor_atributo_ids=[ids["talle_1_id"], ids["blanco_id"]],
            )
        )

    assert error.value.code == VARIANT_ALREADY_EXISTS
    assert len(service.list()) == 1
