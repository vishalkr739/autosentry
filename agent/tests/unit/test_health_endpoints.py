import importlib
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import autosentry_agent.main as main_module
from autosentry_agent.main import create_app, _mcp_lifespan
from autosentry_agent.mcp.session_manager import McpSessionManager


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


class _FakeSessionManager:
    """Minimal stand-in for McpSessionManager exposing only the interface
    the lifespan wiring in main.py depends on (is_ready/start/stop), so
    these tests never launch a real tigergraph-mcp subprocess or connect to
    a real TigerGraph instance."""

    def __init__(self, start_error: Exception | None = None):
        self._ready = False
        self.start_calls = 0
        self.stop_calls = 0
        self._start_error = start_error

    def is_ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        self._ready = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self._ready = False


def _working_engine() -> MagicMock:
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    return engine


def test_lifespan_start_failure_is_caught_and_ready_stays_not_ready():
    """If McpSessionManager.start() raises at boot (e.g. tigergraph-mcp
    unreachable), the app must still start and /ready must report
    mcp_ok=False instead of the process crashing."""
    manager = _FakeSessionManager(start_error=RuntimeError("tigergraph-mcp unreachable"))
    app = create_app(
        engine=_working_engine(),
        mcp_ready_check=manager.is_ready,
        lifespan=_mcp_lifespan(manager),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert manager.start_calls == 1
    assert response.status_code == 503
    body = response.json()
    assert body["db_ok"] is True
    assert body["mcp_ok"] is False


def test_lifespan_successful_start_makes_ready_report_mcp_ok():
    """A successful McpSessionManager.start() at boot must flip is_ready()
    to True before requests are served, so /ready reports mcp_ok=True."""
    manager = _FakeSessionManager()
    app = create_app(
        engine=_working_engine(),
        mcp_ready_check=manager.is_ready,
        lifespan=_mcp_lifespan(manager),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert manager.start_calls == 1
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_lifespan_stops_session_manager_on_shutdown():
    manager = _FakeSessionManager()
    app = create_app(
        engine=_working_engine(),
        mcp_ready_check=manager.is_ready,
        lifespan=_mcp_lifespan(manager),
    )

    with TestClient(app):
        pass

    assert manager.stop_calls == 1


def test_lifespan_stop_failure_does_not_raise_during_shutdown():
    """A failure tearing down an already-degraded session on shutdown must
    not blow up the shutdown sequence either."""

    class _StopFailsSessionManager(_FakeSessionManager):
        async def stop(self) -> None:
            self.stop_calls += 1
            raise RuntimeError("teardown failed")

    manager = _StopFailsSessionManager()
    app = create_app(
        engine=_working_engine(),
        mcp_ready_check=manager.is_ready,
        lifespan=_mcp_lifespan(manager),
    )

    with TestClient(app):
        pass  # must not raise

    assert manager.stop_calls == 1


def _reload_main():
    """Reload main.py under the current (monkeypatched) environment. The
    caller must restore via `_restore_main(monkeypatch)` in a finally block."""
    return importlib.reload(main_module)


def _restore_main(monkeypatch):
    # Restore module state before this monkeypatch's fixture teardown, so
    # later tests see the module wired from the real environment again
    # rather than this test's overrides.
    monkeypatch.undo()
    importlib.reload(main_module)


def test_module_level_session_manager_uses_documented_defaults(monkeypatch):
    # Environment-independent: a developer's own TG_GRAPHNAME /
    # AUTOSENTRY_TENANT_ID must not change what "the defaults" are.
    monkeypatch.delenv("TG_GRAPHNAME", raising=False)
    monkeypatch.delenv("AUTOSENTRY_TENANT_ID", raising=False)
    monkeypatch.delenv("TG_HOST", raising=False)
    monkeypatch.delenv("TG_USERNAME", raising=False)
    try:
        reloaded = _reload_main()
        assert isinstance(reloaded._session_manager, McpSessionManager)
        assert reloaded._session_manager.tenant_id == "default"
        assert reloaded._session_manager.graph_name == "AutosentrySandbox"
        assert reloaded._session_manager._tg_host is None
        assert reloaded._session_manager._tg_username is None
        assert reloaded.app is not None
    finally:
        _restore_main(monkeypatch)


def test_module_level_session_manager_reads_tg_host_and_username(monkeypatch):
    monkeypatch.setenv("TG_HOST", "https://tg.example.internal")
    monkeypatch.setenv("TG_USERNAME", "autosentry_svc")
    try:
        reloaded = _reload_main()
        assert reloaded._session_manager._tg_host == "https://tg.example.internal"
        assert reloaded._session_manager._tg_username == "autosentry_svc"
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
        assert reloaded._session_manager._secrets is providers[0]
    finally:
        _restore_main(monkeypatch)


def test_module_level_session_manager_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("AUTOSENTRY_TENANT_ID", "tenant-42")
    monkeypatch.setenv("TG_GRAPHNAME", "CustomGraph")
    try:
        reloaded = _reload_main()
        assert reloaded._session_manager.tenant_id == "tenant-42"
        assert reloaded._session_manager.graph_name == "CustomGraph"
    finally:
        _restore_main(monkeypatch)
