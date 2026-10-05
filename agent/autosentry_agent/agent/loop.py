"""The bounded tool-calling loop an investigation lane runs; a LaneSpec says what differs.

Follows savanna-agent's `agent/specialist_loop.py`: one loop, configured
per lane. Each step the model either calls tools or answers. Every call is
checked against the risk table before it runs (this lane may only read),
every result goes back to the model as compact JSON, and the final answer
is checked against what the tools actually returned before it is accepted.
"""

import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from ..prompts.investigator import ADHOC_BUDGET_SPENT, CITATION_CORRECTION, OUT_OF_STEPS
from ..tools.base import ToolSpec
from ..tools.results import InvestigationResult, failure
from ..tools.tool_risk import Risk
from .grounding import EvidenceRegistry

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], None]

_RESULT_CHARS = 12_000
_ARG_CHARS = 500


@dataclass(frozen=True)
class LaneSpec:
    name: str
    system_prompt: str
    tools: tuple[ToolSpec, ...]
    max_steps: int = 12
    max_failed_adhoc_queries: int = 2
    # Wall-clock budget for one question: past it, no more tool calls,
    # the model answers from what it has (as when out of steps).
    max_seconds: float = 240.0


@dataclass
class AgentResult:
    answer: str
    outcome: str  # answered | out_of_steps | out_of_time
    cited_evidence: list[dict] = field(default_factory=list)
    cited_documents: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    unbacked: list[str] = field(default_factory=list)
    steps: int = 0
    trace: list[dict] = field(default_factory=list)
    registry: dict = field(default_factory=dict)  # what the thread's tools have returned so far


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )


def _for_model(result: InvestigationResult) -> str:
    """A tool result as the model sees it: everything it needs to reason and cite, bounded."""
    evidence: dict[str, list[str]] = {}
    for item in result["evidence"][:200]:
        evidence.setdefault(item["type"], []).append(item["id"])
    payload = {
        "ok": result["ok"],
        "summary": result["summary"],
        "error": result["error"],
        "data": result["data"],
        "evidence_ids_by_type": evidence,
        "citations": [
            {"cite_as": f"doc:{c['doc_id']}", "title": c["title"], "url": c["url"], "text": c["text"]}
            for c in result["citations"]
        ],
    }
    rendered = json.dumps(payload, default=str)
    if len(rendered) > _RESULT_CHARS:
        payload["data"] = {"truncated": True, "preview": json.dumps(result["data"], default=str)[:_RESULT_CHARS // 2]}
        rendered = json.dumps(payload, default=str)
    return rendered


class _Trace:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.records: list[dict] = []

    def record(self, kind: str, name: str, *, step: int, ok: bool, started: float, **extra: Any) -> None:
        entry = {
            "seq": len(self.records) + 1,
            "turn_id": self.turn_id,
            "kind": kind,
            "name": name,
            "step": step,
            "ok": ok,
            "duration_ms": round((time.monotonic() - started) * 1000),
            **extra,
        }
        self.records.append(entry)
        logger.info("trace %s", json.dumps(entry, default=str))


async def run_lane(
    spec: LaneSpec,
    llm: BaseChatModel,
    question: str,
    *,
    history: Sequence[BaseMessage] = (),
    emit: Emit | None = None,
    turn_id: str | None = None,
    registry: EvidenceRegistry | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """Work `question` until answered or out of steps.

    `registry` is what earlier turns in the thread already gathered: an
    answer may cite it, and this turn's results are added to it.
    """
    emit = emit or (lambda event: None)
    trace = _Trace(turn_id or uuid.uuid4().hex)
    tools = {tool.name: tool for tool in spec.tools}
    bound = llm.bind_tools([
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema()}}
        for t in spec.tools
    ])
    messages: list[BaseMessage] = [SystemMessage(spec.system_prompt), *history, HumanMessage(question)]
    registry = registry or EvidenceRegistry()
    failed_adhoc = 0
    corrected = False
    deadline = clock() + spec.max_seconds
    outcome = "out_of_steps"

    for step in range(1, spec.max_steps + 1):
        if clock() >= deadline:
            outcome = "out_of_time"
            break
        started = time.monotonic()
        reply = await bound.ainvoke(messages)
        usage = getattr(reply, "usage_metadata", None) or {}
        trace.record("llm", spec.name, step=step, ok=True, started=started,
                     tool_calls=len(getattr(reply, "tool_calls", []) or []), tokens=usage.get("total_tokens"))
        messages.append(reply)

        calls = getattr(reply, "tool_calls", None) or []
        if calls:
            for call in calls:
                name, args = call["name"], call.get("args") or {}
                result = await _call_tool(tools, name, args, spec, failed_adhoc, emit, trace, step)
                if name == "run_graph_query" and not result["ok"]:
                    failed_adhoc += 1
                registry.add(result)
                messages.append(ToolMessage(content=_for_model(result), tool_call_id=call["id"]))
            continue

        answer = _text(reply).strip()
        unbacked = registry.unbacked(answer)
        if unbacked and not corrected:
            corrected = True
            messages.append(HumanMessage(CITATION_CORRECTION.format(ids=", ".join(unbacked))))
            continue
        return _finish(answer, "answered", registry, unbacked, step, trace)

    # Out of steps or time: one last call, without tools, to answer from what was gathered.
    started = time.monotonic()
    messages.append(HumanMessage(OUT_OF_STEPS))
    reply = await llm.ainvoke(messages)
    trace.record("llm", spec.name, step=spec.max_steps + 1, ok=True, started=started, tool_calls=0)
    answer = _text(reply).strip()
    return _finish(answer, outcome, registry, registry.unbacked(answer), spec.max_steps, trace)


async def _call_tool(
    tools: dict[str, ToolSpec],
    name: str,
    args: dict[str, Any],
    spec: LaneSpec,
    failed_adhoc: int,
    emit: Emit,
    trace: _Trace,
    step: int,
) -> InvestigationResult:
    started = time.monotonic()
    tool = tools.get(name)
    if tool is None:
        result = failure(f"{name} is not a tool of this investigation", f"available tools: {sorted(tools)}")
    elif tool.risk is not Risk.READ:
        result = failure(f"{name} was refused", "this investigation may only read; it changes nothing")
    elif name == "run_graph_query" and failed_adhoc >= spec.max_failed_adhoc_queries:
        result = failure("run_graph_query was refused", ADHOC_BUDGET_SPENT.format(count=failed_adhoc))
    else:
        emit({"type": "activity", "data": {"tool": name, "status": "started", "args": args}})
        result = await tool.invoke(args)
        emit({"type": "activity", "data": {
            "tool": name, "status": "done" if result["ok"] else "failed", "summary": result["summary"],
        }})
    trace.record(
        "tool", name, step=step, ok=result["ok"], started=started,
        args=json.dumps(args, default=str)[:_ARG_CHARS], summary=result["summary"], error=result["error"],
        evidence=len(result["evidence"]),
    )
    return result


def _finish(
    answer: str, outcome: str, registry: EvidenceRegistry, unbacked: list[str], steps: int, trace: _Trace
) -> AgentResult:
    evidence, documents = registry.cited(answer)
    return AgentResult(
        answer=answer,
        outcome=outcome,
        cited_evidence=[dict(e) for e in evidence],
        cited_documents=documents,
        evidence=[dict(e) for e in registry.evidence.values()],
        unbacked=unbacked,
        steps=steps,
        trace=trace.records,
        registry=registry.to_state(),
    )
