import logging
import os
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncIterator, Callable

from fastapi import FastAPI, Response
from sqlalchemy import Engine, text

from .config import get_secrets_provider
from .db.session import get_engine
from .mcp.session_manager import McpSessionManager

logger = logging.getLogger(__name__)


def create_app(
    engine: Engine,
    mcp_ready_check: Callable[[], bool],
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready(response: Response):
        db_ok = True
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False

        mcp_ok = mcp_ready_check()

        if db_ok and mcp_ok:
            return {"status": "ready"}

        response.status_code = 503
        return {"status": "not-ready", "db_ok": db_ok, "mcp_ok": mcp_ok}

    return app


def _mcp_lifespan(
    session_manager: McpSessionManager,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Builds a FastAPI lifespan that starts/stops `session_manager` around
    the app's life.

    A failure to start the MCP session at boot (e.g. `tigergraph-mcp`
    unreachable, which is expected and common in local/dev environments
    without a real TigerGraph workspace configured) is caught and logged
    rather than propagated: the whole point of wiring readiness through
    `session_manager.is_ready` is that `/ready` degrades gracefully to
    `mcp_ok=False` instead of the process failing to start.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await session_manager.start()
        except Exception:
            logger.exception(
                "McpSessionManager.start() failed at startup; /ready will "
                "report mcp_ok=False until the session recovers."
            )
        try:
            yield
        finally:
            try:
                await session_manager.stop()
            except Exception:
                logger.exception("McpSessionManager.stop() failed during shutdown.")

    return lifespan


# Real wiring used by the container's entrypoint (Task 1's Dockerfile CMD).
_secrets_provider = get_secrets_provider()
_engine = get_engine(_secrets_provider.get_secret("DATABASE_URL"))
# Non-secret TigerGraph connection config is read from the environment, the
# same way TG_GRAPHNAME already is; only TG_PASSWORD goes through the
# SecretsProvider. These are forwarded explicitly to the tigergraph-mcp
# subprocess (which never inherits this process's environment wholesale).
_session_manager = McpSessionManager(
    tenant_id=os.environ.get("AUTOSENTRY_TENANT_ID", "default"),
    graph_name=os.environ.get("TG_GRAPHNAME", "AutosentrySandbox"),
    secrets_provider=_secrets_provider,
    tg_host=os.environ.get("TG_HOST"),
    tg_username=os.environ.get("TG_USERNAME"),
)
app = create_app(
    engine=_engine,
    mcp_ready_check=_session_manager.is_ready,
    lifespan=_mcp_lifespan(_session_manager),
)
