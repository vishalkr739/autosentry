import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from .agent.graph import Investigator, build_investigation_graph, investigator_lane
from .config import GraphDataSettings, KnowledgeSettings, LLMSettings, get_secrets_provider
from .db.session import get_engine
from .ingestion.corpus import load_corpus
from .ingestion.graphrag_current import CurrentGraphragBackend
from .llm import LLMConfigError, build_chat_model
from .mcp.transport import GraphDataMcpClient
from .secrets.base import SecretsProvider
from .tools import build_tools

logger = logging.getLogger(__name__)

_DEFAULT_CORPUS = Path(__file__).resolve().parent / "ingestion" / "corpora" / "regulatory.toml"


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


def _error(status: int, code: str, detail: str, request: Request) -> JSONResponse:
    """savanna-agent's error shape: {detail, code, request_id}."""
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "code": code, "request_id": request.state.request_id},
    )


def create_app(
    engine: Engine,
    mcp_ready_check: Callable[[], Awaitable[bool]],
    investigator: Investigator | None = None,
    investigator_unavailable: str = "the investigator is not configured",
) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

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

    @app.post("/investigate")
    async def investigate(body: InvestigateRequest, request: Request):
        if investigator is None:
            return _error(503, "investigator_unavailable", investigator_unavailable, request)
        try:
            return await investigator.ask(body.question, body.thread_id)
        except Exception as exc:
            logger.exception("investigation failed")
            return _error(502, "investigation_failed", f"the investigation failed: {exc}", request)

    @app.post("/investigate/stream")
    async def investigate_stream(body: InvestigateRequest, request: Request):
        """NDJSON: one event per line, ending in `final` or `error`."""
        if investigator is None:
            return _error(503, "investigator_unavailable", investigator_unavailable, request)
        active = investigator

        async def events() -> AsyncIterator[bytes]:
            try:
                async for event in active.stream(body.question, body.thread_id):
                    yield (json.dumps(event, default=str) + "\n").encode()
            except Exception as exc:
                logger.exception("streamed investigation failed")
                error = {"type": "error", "data": {
                    "detail": f"the investigation failed: {exc}",
                    "code": "investigation_failed",
                    "request_id": request.state.request_id,
                }}
                yield (json.dumps(error) + "\n").encode()

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return app


def build_investigator(
    graph_data: GraphDataMcpClient, secrets: SecretsProvider
) -> tuple[Investigator | None, str]:
    """The real investigator, or None and why not (the app still starts, /investigate says why)."""
    try:
        llm = build_chat_model(LLMSettings(), secrets)
    except LLMConfigError as exc:
        return None, str(exc)

    knowledge = KnowledgeSettings()
    backend = None
    corpus = None
    try:
        token = secrets.get_secret("TG_JWT_TOKEN")
        backend = CurrentGraphragBackend(knowledge.graphrag_base_url, token)
        corpus = load_corpus(Path(knowledge.knowledge_corpus) if knowledge.knowledge_corpus else _DEFAULT_CORPUS)
    except KeyError:
        logger.warning("no TG_JWT_TOKEN for graphrag: knowledge search is off")
    tools = build_tools(graph_data, knowledge_backend=backend, corpus=corpus)
    return Investigator(build_investigation_graph(llm, investigator_lane(tools))), ""


# Real wiring used by the container's entrypoint (Task 1's Dockerfile CMD).
_secrets_provider = get_secrets_provider()
_engine = get_engine(_secrets_provider.get_secret("DATABASE_URL"))
_graph_data = GraphDataMcpClient(GraphDataSettings(), _secrets_provider)
_investigator, _unavailable = build_investigator(_graph_data, _secrets_provider)
if _investigator is None:
    logger.warning("investigator unavailable: %s", _unavailable)
app = create_app(
    engine=_engine,
    mcp_ready_check=_graph_data.is_ready,
    investigator=_investigator,
    investigator_unavailable=_unavailable,
)
