"""The investigation graph and the service the API calls.

Phase 1 is one node, the Fraud Investigator lane (spec section 14); Phase
2's Orchestration Agent adds a router in front of more lanes running the
same loop. A checkpointer keyed by thread_id carries the conversation, so
a follow-up question sees the earlier turns: the question and answer of
each turn, not every tool exchange, which keeps the history small.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from ..prompts.investigator import INVESTIGATOR_SYSTEM_PROMPT
from ..tools.base import ToolSpec
from .grounding import EvidenceRegistry
from .loop import LaneSpec, run_lane


class InvestigationState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    turn_id: str
    result: dict[str, Any]
    registry: dict[str, Any]


def investigator_lane(tools: list[ToolSpec], *, max_steps: int = 12) -> LaneSpec:
    return LaneSpec("fraud_investigator", INVESTIGATOR_SYSTEM_PROMPT, tuple(tools), max_steps=max_steps)


def build_investigation_graph(
    llm: BaseChatModel, lane: LaneSpec, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    async def investigate(state: InvestigationState) -> InvestigationState:
        writer = get_stream_writer()
        question = state["question"]
        result = await run_lane(
            lane,
            llm,
            question,
            history=state.get("messages", []),
            emit=writer,
            turn_id=state.get("turn_id"),
            registry=EvidenceRegistry.from_state(state.get("registry")),
        )
        registry = result.registry
        result.registry = {}  # kept in the thread's state, not repeated in every response
        return {
            "messages": [HumanMessage(question), AIMessage(result.answer)],
            "result": asdict(result),
            "registry": registry,
        }

    workflow = StateGraph(InvestigationState)
    workflow.add_node("investigate", investigate)
    workflow.set_entry_point("investigate")
    workflow.add_edge("investigate", END)
    return workflow.compile(checkpointer=checkpointer or InMemorySaver())


class Investigator:
    """What the API talks to: ask a question in a thread, or stream it."""

    def __init__(self, graph: CompiledStateGraph) -> None:
        self._graph = graph

    def _start(
        self, question: str, thread_id: str | None
    ) -> tuple[InvestigationState, RunnableConfig, str, str]:
        thread_id = thread_id or uuid.uuid4().hex
        turn_id = uuid.uuid4().hex
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        state: InvestigationState = {"question": question, "turn_id": turn_id}
        return state, config, thread_id, turn_id

    async def ask(self, question: str, thread_id: str | None = None) -> dict[str, Any]:
        state, config, thread_id, turn_id = self._start(question, thread_id)
        final = await self._graph.ainvoke(state, config)
        return {"thread_id": thread_id, "turn_id": turn_id, **final["result"]}

    async def stream(self, question: str, thread_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """NDJSON-ready events in savanna-agent's vocabulary: turn_start, activity, message, final."""
        state, config, thread_id, turn_id = self._start(question, thread_id)
        yield {"type": "turn_start", "data": {"thread_id": thread_id, "turn_id": turn_id}}
        # A thread's state still holds the previous turn's result until this
        # turn's node returns, so only the last snapshot is this turn's.
        last: dict[str, Any] = {}
        async for mode, chunk in self._graph.astream(state, config, stream_mode=["custom", "values"]):
            if mode == "custom" and isinstance(chunk, dict):
                yield chunk
            elif mode == "values" and isinstance(chunk, dict):
                last = chunk
        result = last.get("result")
        if result is None:
            raise RuntimeError("the investigation ended without a result")
        yield {"type": "message", "data": {"text": result["answer"]}}
        yield {"type": "final", "data": {"thread_id": thread_id, "turn_id": turn_id, **result}}
