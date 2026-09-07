from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app


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


def create_textil_catalog(client: TestClient) -> dict[str, int]:
    categoria = client.post("/api/categorias", json={"nombre": "Remeras"}).json()
    talle = client.post("/api/atributos", json={"nombre": "Talle", "codigo": "TAL"}).json()
    color = client.post("/api/atributos", json={"nombre": "Color", "codigo": "COL"}).json()
    talle_1 = client.post(
        "/api/atributos/valores",
        json={"atributo_id": talle["id"], "valor": "1", "codigo": "01"},
    ).json()
    talle_2 = client.post(
        "/api/atributos/valores",
        json={"atributo_id": talle["id"], "valor": "2", "codigo": "02"},
    ).json()
    blanco = client.post(
        "/api/atributos/valores",
        json={"atributo_id": color["id"], "valor": "Blanco", "codigo": "01"},
    ).json()
    negro = client.post(
        "/api/atributos/valores",
        json={"atributo_id": color["id"], "valor": "Negro", "codigo": "02"},
    ).json()
    familia = client.post(
        "/api/familias-atributos",
        json={"nombre": "Textil", "descripcion": "Indumentaria con talle y color"},
    ).json()
    client.post(
        f"/api/familias-atributos/{familia['id']}/atributos",
        json={"atributo_id": talle["id"], "orden": 1},
    )
    client.post(
        f"/api/familias-atributos/{familia['id']}/atributos",
        json={"atributo_id": color["id"], "orden": 2},
    )
    articulo = client.post(
        "/api/articulos",
        json={
            "codigo": "123456",
            "nombre": "Remera lisa",
            "categoria_id": categoria["id"],
            "familia_atributos_id": familia["id"],
        },
    ).json()
    client.post(
        f"/api/articulos/{articulo['id']}/atributos",
        json={"atributo_id": talle["id"], "orden": 1},
    )
    client.post(
        f"/api/articulos/{articulo['id']}/atributos",
        json={"atributo_id": color["id"], "orden": 2},
    )
    return {
        "categoria_id": categoria["id"],
        "talle_id": talle["id"],
        "color_id": color["id"],
        "talle_1_id": talle_1["id"],
        "talle_2_id": talle_2["id"],
        "blanco_id": blanco["id"],
        "negro_id": negro["id"],
        "familia_id": familia["id"],
        "articulo_id": articulo["id"],
    }


def preview_payload(ids: dict[str, int]) -> dict[str, list[dict[str, object]]]:
    return {
        "atributos": [
            {
                "atributo_id": ids["talle_id"],
                "valor_atributo_ids": [ids["talle_1_id"], ids["talle_2_id"]],
            },
            {
                "atributo_id": ids["color_id"],
                "valor_atributo_ids": [ids["blanco_id"], ids["negro_id"]],
            },
        ]
    }


def test_crear_familia_textil_y_asociar_talle_color(client: TestClient) -> None:
    ids = create_textil_catalog(client)

    response = client.get(f"/api/familias-atributos/{ids['familia_id']}/atributos")

    assert response.status_code == 200
    assert [item["atributo_id"] for item in response.json()] == [ids["talle_id"], ids["color_id"]]


def test_preview_dos_por_dos_y_codigos_correctos(client: TestClient) -> None:
    ids = create_textil_catalog(client)

    response = client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/preview",
        json=preview_payload(ids),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4
    assert {item["codigo_barra_propuesto"] for item in data} == {
        "1234560101",
        "1234560102",
        "1234560201",
        "1234560202",
    }
    assert {item["estado"] for item in data} == {"NUEVA"}


def test_preview_marca_combinacion_existente(client: TestClient) -> None:
    ids = create_textil_catalog(client)
    client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={
            "combinaciones": [
                {"valor_atributo_ids": [ids["talle_1_id"], ids["blanco_id"]]},
            ]
        },
    )

    response = client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/preview",
        json=preview_payload(ids),
    )

    existing = [item for item in response.json() if item["estado"] == "EXISTENTE"]
    assert response.status_code == 200
    assert len(existing) == 1
    assert existing[0]["codigo_barra_propuesto"] == "1234560101"


def test_evitar_variante_duplicada(client: TestClient) -> None:
    ids = create_textil_catalog(client)
    payload = {"combinaciones": [{"valor_atributo_ids": [ids["talle_1_id"], ids["blanco_id"]]}]}

    first = client.post(f"/api/articulos/{ids['articulo_id']}/variantes/generate", json=payload)
    duplicate = client.post(f"/api/articulos/{ids['articulo_id']}/variantes/generate", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_evitar_codigo_barra_duplicado(client: TestClient) -> None:
    ids = create_textil_catalog(client)
    client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={"combinaciones": [{"valor_atributo_ids": [ids["talle_1_id"], ids["blanco_id"]]}]},
    )

    response = client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={
            "combinaciones": [
                {
                    "valor_atributo_ids": [ids["talle_2_id"], ids["negro_id"]],
                    "codigo_barra": "1234560101",
                }
            ]
        },
    )

    assert response.status_code == 409


def test_permitir_excluir_una_combinacion(client: TestClient) -> None:
    ids = create_textil_catalog(client)

    response = client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={
            "combinaciones": [
                {"valor_atributo_ids": [ids["talle_1_id"], ids["blanco_id"]]},
                {"valor_atributo_ids": [ids["talle_2_id"], ids["negro_id"]]},
            ]
        },
    )

    assert response.status_code == 201
    assert len(response.json()) == 2


def test_transaccion_completa_si_falla_una_variante(client: TestClient) -> None:
    ids = create_textil_catalog(client)
    client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={"combinaciones": [{"valor_atributo_ids": [ids["talle_1_id"], ids["blanco_id"]]}]},
    )

    response = client.post(
        f"/api/articulos/{ids['articulo_id']}/variantes/generate",
        json={
            "combinaciones": [
                {"valor_atributo_ids": [ids["talle_1_id"], ids["negro_id"]]},
                {
                    "valor_atributo_ids": [ids["talle_2_id"], ids["blanco_id"]],
                    "codigo_barra": "1234560101",
                },
            ]
        },
    )
    variantes = client.get("/api/variantes").json()

    assert response.status_code == 409
    assert len(variantes) == 1


def test_articulo_sin_atributos(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Accesorios"}).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "LLAVERO", "nombre": "Llavero", "categoria_id": categoria["id"]},
    ).json()

    preview = client.post(f"/api/articulos/{articulo['id']}/variantes/preview", json={"atributos": []})
    generated = client.post(
        f"/api/articulos/{articulo['id']}/variantes/generate",
        json={"combinaciones": [{"valor_atributo_ids": []}]},
    )

    assert preview.status_code == 200
    assert preview.json()[0]["codigo_barra_propuesto"] == "LLAVERO"
    assert generated.status_code == 201
    assert generated.json()[0]["codigo_barra"] == "LLAVERO"


def test_variante_con_un_unico_atributo(client: TestClient) -> None:
    categoria = client.post("/api/categorias", json={"nombre": "Calzado"}).json()
    numero = client.post("/api/atributos", json={"nombre": "Numero", "codigo": "NUM"}).json()
    numero_40 = client.post(
        "/api/atributos/valores",
        json={"atributo_id": numero["id"], "valor": "40", "codigo": "40"},
    ).json()
    articulo = client.post(
        "/api/articulos",
        json={"codigo": "ZAPA", "nombre": "Zapatilla", "categoria_id": categoria["id"]},
    ).json()
    client.post(f"/api/articulos/{articulo['id']}/atributos", json={"atributo_id": numero["id"]})

    response = client.post(
        f"/api/articulos/{articulo['id']}/variantes/generate",
        json={"combinaciones": [{"valor_atributo_ids": [numero_40["id"]]}]},
    )

    assert response.status_code == 201
    assert response.json()[0]["codigo_barra"] == "ZAPA40"
