from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app import models  # noqa: F401


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
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

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_catalog_base(client: TestClient) -> dict[str, int]:
    categoria = client.post("/api/categorias", json={"nombre": "Remeras"}).json()
    marca = client.post("/api/marcas", json={"nombre": "ARD"}).json()
    talle = client.post("/api/atributos", json={"nombre": "Talle", "codigo": "TAL"}).json()
    color = client.post("/api/atributos", json={"nombre": "Color", "codigo": "COL"}).json()
    talle_3 = client.post(
        "/api/atributos/valores",
        json={"atributo_id": talle["id"], "valor": "3", "codigo": "03"},
    ).json()
    negro = client.post(
        "/api/atributos/valores",
        json={"atributo_id": color["id"], "valor": "Negro", "codigo": "01"},
    ).json()
    articulo = client.post(
        "/api/articulos",
        json={
            "codigo": "123456",
            "nombre": "Remera lisa",
            "categoria_id": categoria["id"],
            "marca_id": marca["id"],
        },
    ).json()
    client.post(f"/api/articulos/{articulo['id']}/atributos", json={"atributo_id": talle["id"]})
    client.post(f"/api/articulos/{articulo['id']}/atributos", json={"atributo_id": color["id"]})
    return {
        "categoria_id": categoria["id"],
        "marca_id": marca["id"],
        "talle_id": talle["id"],
        "color_id": color["id"],
        "talle_3_id": talle_3["id"],
        "negro_id": negro["id"],
        "articulo_id": articulo["id"],
    }


def test_crear_categoria(client: TestClient) -> None:
    response = client.post("/api/categorias", json={"nombre": "Indumentaria"})

    assert response.status_code == 201
    assert response.json()["nombre"] == "Indumentaria"


def test_crear_articulo_y_evitar_codigo_duplicado(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Indumentaria"}).json()
    payload = {"codigo": "ART-001", "nombre": "Remera lisa", "categoria_id": categoria["id"]}

    first = client.post("/api/articulos", json=payload)
    duplicate = client.post("/api/articulos", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_asignar_atributo_y_crear_valores(client: TestClient) -> None:
    ids = create_catalog_base(client)

    atributos = client.get(f"/api/articulos/{ids['articulo_id']}/atributos")
    valores = client.get("/api/atributos/valores")

    assert atributos.status_code == 200
    assert len(atributos.json()) == 2
    assert valores.status_code == 200
    assert {item["valor"] for item in valores.json()} == {"3", "Negro"}


def test_crear_variante_y_buscar_por_codigo_barra(client: TestClient) -> None:
    ids = create_catalog_base(client)

    response = client.post(
        "/api/variantes",
        json={
            "articulo_id": ids["articulo_id"],
            "codigo_barra": "1234560301",
            "valor_atributo_ids": [ids["talle_3_id"], ids["negro_id"]],
        },
    )
    barcode = client.get("/api/variantes/barcode/1234560301")

    assert response.status_code == 201
    assert barcode.status_code == 200
    assert barcode.json()["codigo_barra"] == "1234560301"
    assert barcode.json()["articulo"]["codigo"] == "123456"


def test_impedir_codigo_barra_duplicado(client: TestClient) -> None:
    ids = create_catalog_base(client)
    payload = {
        "articulo_id": ids["articulo_id"],
        "codigo_barra": "1234560301",
        "valor_atributo_ids": [ids["talle_3_id"], ids["negro_id"]],
    }

    first = client.post("/api/variantes", json=payload)
    duplicate = client.post("/api/variantes", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_asociar_varios_proveedores_al_mismo_articulo(client: TestClient) -> None:
    ids = create_catalog_base(client)
    proveedor_a = client.post("/api/proveedores", json={"codigo": "PROV-1", "nombre": "Proveedor A"}).json()
    proveedor_b = client.post("/api/proveedores", json={"codigo": "PROV-2", "nombre": "Proveedor B"}).json()

    first = client.post(
        "/api/proveedores/articulos",
        json={"articulo_id": ids["articulo_id"], "proveedor_id": proveedor_a["id"]},
    )
    second = client.post(
        "/api/proveedores/articulos",
        json={"articulo_id": ids["articulo_id"], "proveedor_id": proveedor_b["id"]},
    )
    asociados = client.get(f"/api/proveedores/articulos/{ids['articulo_id']}")

    assert first.status_code == 201
    assert second.status_code == 201
    assert asociados.status_code == 200
    assert len(asociados.json()) == 2
