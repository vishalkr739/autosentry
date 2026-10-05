import importlib
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import autosentry_agent.main as main_module
from autosentry_agent.main import create_app
from autosentry_agent.mcp.transport import GraphDataMcpClient


def _ready(value: bool):
    async def check() -> bool:
        return value

    return check


def _working_engine() -> MagicMock:
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    return engine


def test_health_always_returns_200():
    app = create_app(engine=MagicMock(), mcp_ready_check=_ready(False))
    assert TestClient(app).get("/health").status_code == 200


def test_ready_returns_200_when_db_and_mcp_are_healthy():
    app = create_app(engine=_working_engine(), mcp_ready_check=_ready(True))
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_mcp_not_ready():
    app = create_app(engine=_working_engine(), mcp_ready_check=_ready(False))
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not-ready", "db_ok": True, "mcp_ok": False}


def test_ready_returns_503_when_db_unreachable():
    engine = MagicMock()
    engine.connect.side_effect = Exception("connection refused")
    app = create_app(engine=engine, mcp_ready_check=_ready(True))
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json()["db_ok"] is False


def test_ready_reports_mcp_not_ok_when_the_check_itself_raises():
    """A readiness probe that blows up must degrade /ready, not 500 it."""

    async def broken() -> bool:
        raise RuntimeError("probe crashed")

    app = create_app(engine=_working_engine(), mcp_ready_check=broken)
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json()["mcp_ok"] is False


def _reload_main():
    """Reload main.py under the current (monkeypatched) environment. The
    caller must restore via `_restore_main(monkeypatch)` in a finally block."""
    return importlib.reload(main_module)


def _restore_main(monkeypatch):
    # Restore module state before this monkeypatch's fixture teardown, so
    # later tests see the module wired from the real environment again.
    monkeypatch.undo()
    importlib.reload(main_module)


def test_module_level_client_uses_documented_defaults(monkeypatch):
    for name in ("TG_GRAPHNAME", "TG_HOST", "GRAPH_DATA_MCP_URL", "AUTOSENTRY_ENV"):
        monkeypatch.delenv(name, raising=False)
    try:
        reloaded = _reload_main()
        client = reloaded._graph_data
        assert isinstance(client, GraphDataMcpClient)
        assert client.graph_name == "AutosentrySandbox"
        assert client._settings.graph_data_mcp_url == "http://localhost:8010"
        assert client._settings.autosentry_env == "dev"
        assert reloaded.app is not None
    finally:
        _restore_main(monkeypatch)


def test_module_level_client_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("TG_GRAPHNAME", "CustomGraph")
    monkeypatch.setenv("TG_HOST", "https://tg.example.internal")
    monkeypatch.setenv("GRAPH_DATA_MCP_URL", "http://tigergraph-mcp:8010")
    try:
        client = _reload_main()._graph_data
        assert client.graph_name == "CustomGraph"
        assert client._settings.tg_host == "https://tg.example.internal"
        assert client._settings.graph_data_mcp_url == "http://tigergraph-mcp:8010"
    finally:
        _restore_main(monkeypatch)


def test_module_level_wiring_builds_one_secrets_provider(monkeypatch):
    import autosentry_agent.config as config_module

    real_get_secrets_provider = config_module.get_secrets_provider
    providers = []

    def counting_get_secrets_provider():
        provider = real_get_secrets_provider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(config_module, "get_secrets_provider", counting_get_secrets_provider)
    try:
        reloaded = _reload_main()
        assert len(providers) == 1
        assert reloaded._graph_data._secrets is providers[0]
    finally:
        _restore_main(monkeypatch)
