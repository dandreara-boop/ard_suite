from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.main import app


client = TestClient(app)


def test_root() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"name": settings.app_name, "status": "running"}


def test_health_response_structure(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.api.health.check_database_connection", lambda: True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
        "app": settings.app_name,
    }


def test_health_unavailable_response(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.api.health.check_database_connection", lambda: False)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "status": "error",
            "database": "unavailable",
            "app": settings.app_name,
        }
    }
