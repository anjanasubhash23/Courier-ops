from fastapi.testclient import TestClient

from app.main import create_app
from core.db.session import make_engine
from tests.conftest import make_settings


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "0.1.0"}


def test_ready_when_database_is_up(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "postgres": "ok"}


def test_ready_reports_503_when_postgres_is_down() -> None:
    # Port 1 refuses connections immediately, so this doesn't wait for a timeout.
    settings = make_settings(postgres_port=1)
    with TestClient(create_app(settings, engine=make_engine(settings.database_url))) as c:
        health, ready = c.get("/health"), c.get("/ready")
    assert health.status_code == 200  # liveness doesn't depend on the database
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "postgres": "unavailable"}
