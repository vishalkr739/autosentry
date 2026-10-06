"""The investigation loop, graph and API, driven by a scripted chat model."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict
from unittest.mock import MagicMock

from autosentry_agent.agent.graph import Investigator, build_investigation_graph
from autosentry_agent.agent.grounding import EvidenceRegistry, cited_ids
from autosentry_agent.agent.loop import LaneSpec, run_lane
from autosentry_agent.config import LLMSettings
from autosentry_agent.llm import LLMConfigError, build_chat_model
from autosentry_agent.main import create_app
from autosentry_agent.secrets.base import SecretsProvider
from autosentry_agent.tools.base import ToolSpec
from autosentry_agent.tools.results import InvestigationResult


class ScriptedChatModel(BaseChatModel):
    """Replies from a script, one per call, and records what it was sent."""

    script: list[AIMessage]
    seen: list[list[BaseMessage]] = []
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":  # type: ignore[override]
        self.bound_tools = [t["function"]["name"] for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        reply = self.script.pop(0) if self.script else AIMessage("out of script")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name: str, args: dict | None = None, call_id: str = "c1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


def model(*replies: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(script=list(replies), seen=[], bound_tools=[])


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryArgs(BaseModel):
    query: str


def fake_tool(name: str, result: InvestigationResult, calls: list[str]) -> ToolSpec:
    async def run(params: BaseModel) -> InvestigationResult:
        calls.append(name)
        return result

    return ToolSpec(name, f"the {name} tool", QueryArgs if name == "run_graph_query" else NoArgs, run)


def ok(evidence: list[dict] | None = None, citations: list[dict] | None = None) -> InvestigationResult:
    return InvestigationResult(
        ok=True, summary="found things", data={"x": 1}, error=None,
        evidence=evidence or [], citations=citations or [],  # type: ignore[typeddict-item]
    )


BAD = InvestigationResult(ok=False, summary="rejected", data=None, error="Type Check Error", evidence=[], citations=[])
ACCOUNT = [{"type": "Account", "id": "account-000127"}]
DOC = [{"doc_id": "fincen_sar_faqs_2025", "chunk_id": "c0", "title": "SAR FAQs", "url": "https://x", "text": "file it"}]


def lane(*tools: ToolSpec, max_steps: int = 12) -> LaneSpec:
    return LaneSpec("fraud_investigator", "You investigate.", tuple(tools), max_steps=max_steps)


@pytest.mark.asyncio
async def test_a_tool_call_then_a_grounded_answer():
    calls: list[str] = []
    events: list[dict] = []
    llm = model(call("find_mule_candidates"), AIMessage("The mule is [account-000127]."))
    result = await run_lane(lane(fake_tool("find_mule_candidates", ok(ACCOUNT), calls)), llm, "List mules", emit=events.append)

    assert result.outcome == "answered" and result.answer == "The mule is [account-000127]."
    assert result.cited_evidence == ACCOUNT and result.unbacked == []
    assert calls == ["find_mule_candidates"]
    assert [r["kind"] for r in result.trace] == ["llm", "tool", "llm"]
    assert [e["data"]["status"] for e in events] == ["started", "done"]
    tool_message = llm.seen[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert json.loads(tool_message.content)["evidence_ids_by_type"] == {"Account": ["account-000127"]}


@pytest.mark.asyncio
async def test_only_read_tools_run():
    calls: list[str] = []
    writer = fake_tool("tigergraph__add_nodes", ok(), calls)
    llm = model(call("tigergraph__add_nodes"), call("no_such_tool", call_id="c2"), AIMessage("Nothing changed."))
    result = await run_lane(lane(writer), llm, "Block account X")

    assert calls == []
    refusals = [json.loads(m.content) for m in llm.seen[2] if isinstance(m, ToolMessage)]
    assert "may only read" in refusals[0]["error"]
    assert "available tools" in refusals[1]["error"]
    assert result.outcome == "answered"


@pytest.mark.asyncio
async def test_failed_ad_hoc_queries_are_capped():
    calls: list[str] = []
    adhoc = fake_tool("run_graph_query", BAD, calls)
    llm = model(*(call("run_graph_query", {"query": "PRINT 1;"}, f"c{i}") for i in range(3)), AIMessage("Could not tell."))
    events: list[dict] = []
    await run_lane(lane(adhoc), llm, "Something odd", emit=events.append)

    assert events[1]["data"] == {"tool": "run_graph_query", "status": "failed", "summary": "rejected", "error": "Type Check Error"}
    assert calls == ["run_graph_query", "run_graph_query"]  # the third was refused, not run
    third = json.loads(llm.seen[3][-1].content)
    assert "its limit" in third["error"]


@pytest.mark.asyncio
async def test_an_answer_citing_unknown_ids_gets_one_correction():
    llm = model(AIMessage("It is [account-999999]."), AIMessage("I have no evidence yet."))
    result = await run_lane(lane(), llm, "Who?")

    correction = llm.seen[1][-1]
    assert isinstance(correction, HumanMessage) and "account-999999" in correction.content
    assert result.answer == "I have no evidence yet." and result.unbacked == []


@pytest.mark.asyncio
async def test_unbacked_ids_that_survive_the_correction_are_reported():
    llm = model(AIMessage("It is [account-999999]."), AIMessage("Still [account-999999]."))
    result = await run_lane(lane(), llm, "Who?")
    assert result.unbacked == ["account-999999"]


@pytest.mark.asyncio
async def test_running_out_of_steps_still_answers_without_tools():
    calls: list[str] = []
    tool = fake_tool("find_mule_candidates", ok(ACCOUNT), calls)
    llm = model(call("find_mule_candidates", call_id="a"), call("find_mule_candidates", call_id="b"), AIMessage("Partial: [account-000127]."))
    result = await run_lane(lane(tool, max_steps=2), llm, "Dig forever")

    assert result.outcome == "out_of_steps" and result.cited_evidence == ACCOUNT
    assert "step or time budget" in llm.seen[-1][-1].content


@pytest.mark.asyncio
async def test_running_out_of_time_stops_calling_tools_and_answers():
    calls: list[str] = []
    tool = fake_tool("find_mule_candidates", ok(ACCOUNT), calls)
    now = [0.0]

    def clock() -> float:
        now[0] += 100.0  # every check is another 100 seconds later
        return now[0]

    llm = model(call("find_mule_candidates", call_id="a"), AIMessage("So far: [account-000127]."))
    spec = LaneSpec("fraud_investigator", "You investigate.", (tool,), max_seconds=150)
    result = await run_lane(spec, llm, "Dig", clock=clock)

    assert result.outcome == "out_of_time"
    assert calls == ["find_mule_candidates"]  # the second step never started
    assert result.cited_evidence == ACCOUNT


@pytest.mark.asyncio
async def test_earlier_turns_evidence_may_be_cited():
    prior = EvidenceRegistry()
    prior.add(ok(ACCOUNT, DOC))
    result = await run_lane(lane(), model(AIMessage("As before, [account-000127] [doc:fincen_sar_faqs_2025].")), "And?", registry=prior)

    assert result.unbacked == []
    assert result.cited_documents[0]["title"] == "SAR FAQs"


@pytest.mark.parametrize(
    ("answer", "ids"),
    [
        ("[account-1] and [mule-chain-0000-tx-02, device-ring-0000]", ["account-1", "mule-chain-0000-tx-02", "device-ring-0000"]),
        ("uses [198.51.100.1] per [doc:fincen_sar_faqs_2025]", ["198.51.100.1", "doc:fincen_sar_faqs_2025"]),
        ("see note [1] and [as above] and [account-1]", ["account-1"]),
        ("[account-1] twice [account-1]", ["account-1"]),
    ],
)
def test_cited_ids(answer, ids):
    assert cited_ids(answer) == ids


@pytest.mark.asyncio
async def test_a_thread_carries_history_and_evidence_between_turns():
    calls: list[str] = []
    llm = model(
        call("find_mule_candidates"), AIMessage("Mule [account-000127]."),
        AIMessage("Yes, [account-000127] again."),
    )
    investigator = Investigator(build_investigation_graph(llm, lane(fake_tool("find_mule_candidates", ok(ACCOUNT), calls))))

    first = await investigator.ask("List mules")
    second = await investigator.ask("Is it still a mule?", first["thread_id"])

    assert second["unbacked"] == [] and second["thread_id"] == first["thread_id"]
    sent = llm.seen[-1]
    assert [m.content for m in sent if isinstance(m, HumanMessage)] == ["List mules", "Is it still a mule?"]
    assert "registry" not in second or second["registry"] == {}


@pytest.mark.asyncio
async def test_streaming_emits_savanna_style_events_in_order():
    calls: list[str] = []
    llm = model(call("find_mule_candidates"), AIMessage("Mule [account-000127]."))
    investigator = Investigator(build_investigation_graph(llm, lane(fake_tool("find_mule_candidates", ok(ACCOUNT), calls))))

    events = [e async for e in investigator.stream("List mules")]

    assert [e["type"] for e in events] == ["turn_start", "activity", "activity", "message", "final"]
    assert events[-1]["data"]["answer"] == "Mule [account-000127]."
    assert events[-1]["data"]["thread_id"] == events[0]["data"]["thread_id"]


class FakeInvestigator:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def ask(self, question: str, thread_id: str | None = None) -> dict:
        if self.fail:
            raise RuntimeError("model unreachable")
        return {"answer": f"answered {question}", "thread_id": thread_id or "t1"}

    async def stream(self, question: str, thread_id: str | None = None):
        yield {"type": "turn_start", "data": {"thread_id": "t1"}}
        if self.fail:
            raise RuntimeError("model unreachable")
        yield {"type": "final", "data": {"answer": "done"}}


def _client(investigator: Any = None, why: str = "no LLM_API_KEY configured") -> TestClient:
    return TestClient(create_app(MagicMock(), _ready, investigator, why))


async def _ready() -> bool:
    return True


def test_investigate_answers_and_echoes_the_request_id():
    response = _client(FakeInvestigator()).post("/investigate", json={"question": "who?"}, headers={"x-request-id": "r1"})
    assert response.status_code == 200 and response.json()["answer"] == "answered who?"
    assert response.headers["x-request-id"] == "r1"


def test_investigate_without_an_investigator_says_why():
    response = _client(None).post("/investigate", json={"question": "who?"})
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "investigator_unavailable" and "LLM_API_KEY" in body["detail"] and body["request_id"]


def test_investigate_failure_uses_the_error_shape():
    response = _client(FakeInvestigator(fail=True)).post("/investigate", json={"question": "who?"})
    assert response.status_code == 502
    assert set(response.json()) == {"detail", "code", "request_id"}


def test_investigate_rejects_bad_input():
    assert _client(FakeInvestigator()).post("/investigate", json={"question": ""}).status_code == 422
    assert _client(FakeInvestigator()).post("/investigate", json={"question": "x", "thread_id": "../etc"}).status_code == 422


def test_stream_is_ndjson_and_ends_in_an_error_event_on_failure():
    good = _client(FakeInvestigator()).post("/investigate/stream", json={"question": "who?"})
    assert good.headers["content-type"].startswith("application/x-ndjson")
    assert [json.loads(line)["type"] for line in good.text.splitlines()] == ["turn_start", "final"]

    bad = _client(FakeInvestigator(fail=True)).post("/investigate/stream", json={"question": "who?"})
    last = json.loads(bad.text.splitlines()[-1])
    assert last["type"] == "error" and last["data"]["code"] == "investigation_failed"


class Secrets(SecretsProvider):
    def __init__(self, **values: str) -> None:
        self.values = values

    def get_secret(self, name: str) -> str:
        return self.values[name]

    def get_secret_version(self, name: str) -> str | None:
        return None


def test_chat_model_needs_a_key():
    with pytest.raises(LLMConfigError, match="LLM_API_KEY"):
        build_chat_model(LLMSettings(), Secrets())


def test_chat_model_uses_the_configured_endpoint():
    llm = build_chat_model(LLMSettings(llm_base_url="https://api.groq.com/openai/v1", llm_model="openai/gpt-oss-120b"), Secrets(LLM_API_KEY="k"))
    assert llm.model_name == "openai/gpt-oss-120b"  # type: ignore[attr-defined]
    assert str(llm.openai_api_base).startswith("https://api.groq.com")  # type: ignore[attr-defined]
