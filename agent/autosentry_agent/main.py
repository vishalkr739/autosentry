import os
from typing import Callable

from fastapi import FastAPI, Response
from sqlalchemy import Engine, text

from .db.session import get_engine


def create_app(engine: Engine, mcp_ready_check: Callable[[], bool]) -> FastAPI:
    app = FastAPI()

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


# Real wiring used by the container's entrypoint (Task 1's Dockerfile CMD).
# mcp_ready_check is a stub returning False until Tasks 5/6/7 are unblocked
# and a real McpSessionManager.is_ready is wired in here instead.
_engine = get_engine(os.environ["DATABASE_URL"])
app = create_app(engine=_engine, mcp_ready_check=lambda: False)
