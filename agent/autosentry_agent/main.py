import logging
from typing import Awaitable, Callable

from fastapi import FastAPI, Response
from sqlalchemy import Engine, text

from .config import GraphDataSettings, get_secrets_provider
from .db.session import get_engine
from .mcp.transport import GraphDataMcpClient

logger = logging.getLogger(__name__)


def create_app(engine: Engine, mcp_ready_check: Callable[[], Awaitable[bool]]) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(response: Response):
        db_ok = True
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False

        # tigergraph-mcp runs as its own service and is reached per session,
        # so there is nothing to start at boot: readiness is whether the
        # configured graph's schema can be read through it right now.
        try:
            mcp_ok = await mcp_ready_check()
        except Exception:
            logger.exception("tigergraph-mcp readiness check failed")
            mcp_ok = False

        if db_ok and mcp_ok:
            return {"status": "ready"}

        response.status_code = 503
        return {"status": "not-ready", "db_ok": db_ok, "mcp_ok": mcp_ok}

    return app


# Real wiring used by the container's entrypoint (Task 1's Dockerfile CMD).
_secrets_provider = get_secrets_provider()
_engine = get_engine(_secrets_provider.get_secret("DATABASE_URL"))
_graph_data = GraphDataMcpClient(GraphDataSettings(), _secrets_provider)
app = create_app(engine=_engine, mcp_ready_check=_graph_data.is_ready)
