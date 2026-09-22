from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from autosentry_agent.main import create_app


def test_health_always_returns_200():
    app = create_app(engine=MagicMock(), mcp_ready_check=lambda: False)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_ready_returns_200_when_db_and_mcp_are_healthy():
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    app = create_app(engine=engine, mcp_ready_check=lambda: True)
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200


def test_ready_returns_503_when_mcp_not_ready():
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    app = create_app(engine=engine, mcp_ready_check=lambda: False)
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 503


def test_ready_returns_503_when_db_unreachable():
    engine = MagicMock()
    engine.connect.side_effect = Exception("connection refused")
    app = create_app(engine=engine, mcp_ready_check=lambda: True)
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 503
