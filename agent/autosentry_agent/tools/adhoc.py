"""run_graph_query: the agent's own GSQL, checked before it reaches TigerGraph.

For questions the installed queries don't cover. The agent writes only the
body of an interpreted query; this tool wraps it for the configured graph
(so it can't name another one) and runs it only if it passes:

1. read-only: no data or schema changes (INSERT, DELETE, attribute
   assignment, CREATE/DROP, loading, file output);
2. schema-valid: every vertex type, edge type and attribute it names
   exists in the live graph schema;
3. compiles: TigerGraph's own parser and semantic check accept it (a
   failure comes back as an error the agent can fix; the agent loop caps
   how many times it may retry);
4. bounded: a client-side timeout and a cap on how much result comes back.

These checks establish that a query is safe and well-formed, not that it
answers the question asked; that is what the golden-query evals measure.
The timeout stops waiting, it does not stop a query already running in
TigerGraph.
"""

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tigergraph_mcp.tool_names import TigerGraphToolName as Tool

from ..mcp.transport import GraphDataMcpClient
from .base import ToolSpec
from .results import InvestigationResult, collect_evidence, failure

_FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INSTALL", "RUN", "LOAD",
    "USE", "GRANT", "REVOKE", "INTERPRET", "TO_CSV", "FILE", "IMPORT", "EXPORT",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN) + r")\b", re.IGNORECASE)
# `v.attr = x`, `v.attr += x`: writing an attribute. `v.@acc` is a local
# accumulator (allowed), and `==` is a comparison.
_ATTRIBUTE_WRITE_RE = re.compile(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*\s*(?:\+|-)?=(?!=)")
_SEED_RE = re.compile(r"\b([A-Za-z_]\w*)\.\*")
_TYPED_ALIAS_RE = re.compile(r"\b([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)")
_EDGE_RE = re.compile(r"-\(\s*([^)]*?)\s*\)-")
_VERTEX_PARAM_RE = re.compile(r"\bVERTEX\s*<\s*([A-Za-z_]\w*)\s*>", re.IGNORECASE)
_DOT_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b(?!\s*\()")
_BUILTIN_ATTRIBUTES = {"type", "id"}


class RunGraphQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=10,
        max_length=8000,
        description=(
            "The BODY of a read-only GSQL interpreted query, without the INTERPRET QUERY header "
            "or outer braces, e.g. 'a = {Account.*}; big = SELECT t FROM a:s -(INITIATED>)- "
            "Transaction:t WHERE t.amount > 5000; PRINT big[big.amount, big.timestamp];'"
        ),
    )


def _strip(query: str) -> str:
    """The query without comments and string literals, for checking."""
    query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    query = re.sub(r"//[^\n]*", " ", query)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', query)


class _Schema:
    def __init__(self, schema: dict[str, Any]) -> None:
        self.vertex_attributes: dict[str, set[str]] = {
            v["Name"]: {a["AttributeName"] for a in v.get("Attributes", [])}
            for v in schema.get("VertexTypes", [])
        }
        self.edge_attributes: dict[str, set[str]] = {}
        for e in schema.get("EdgeTypes", []):
            attributes = {a["AttributeName"] for a in e.get("Attributes", [])}
            self.edge_attributes[e["Name"]] = attributes
            reverse = (e.get("Config") or {}).get("REVERSE_EDGE")
            if reverse:
                self.edge_attributes[reverse] = attributes


def check_query(query: str, schema: _Schema) -> list[str]:
    """Why `query` may not run; empty when it passes the read-only and schema checks."""
    text = _strip(query)
    problems: list[str] = []

    if found := sorted({m.upper() for m in _FORBIDDEN_RE.findall(text)}):
        problems.append(f"not read-only: uses {', '.join(found)}")
    if writes := _ATTRIBUTE_WRITE_RE.findall(text):
        problems.append(f"not read-only: assigns attributes ({'; '.join(w.strip() for w in writes[:3])})")
    for opening, closing in ("{}", "()", "[]"):
        if text.count(opening) != text.count(closing):
            problems.append(f"unbalanced {opening}{closing}")

    vertex_types = schema.vertex_attributes
    edge_types = schema.edge_attributes
    aliases: dict[str, set[str]] = {}
    unknown: set[str] = set()
    for name in _SEED_RE.findall(text) + _VERTEX_PARAM_RE.findall(text):
        if name not in vertex_types:
            unknown.add(name)
    for type_name, alias in _TYPED_ALIAS_RE.findall(text):
        if type_name in vertex_types:
            aliases[alias] = vertex_types[type_name]
        elif type_name in edge_types:
            aliases[alias] = edge_types[type_name]
    for segment in _EDGE_RE.findall(text):
        names, _, alias = segment.partition(":")
        edge_names = [n.strip().strip("<>").strip() for n in names.split("|") if n.strip().strip("<>").strip()]
        for edge in edge_names:
            if edge not in edge_types:
                unknown.add(edge)
        if alias.strip() and len(edge_names) == 1 and edge_names[0] in edge_types:
            aliases[alias.strip()] = edge_types[edge_names[0]]
    if unknown:
        problems.append(
            f"unknown vertex or edge type(s) {sorted(unknown)}; the graph has vertex types "
            f"{sorted(vertex_types)} and edge types {sorted(edge_types)}"
        )

    bad_attributes = sorted({
        f"{alias}.{attribute}"
        for alias, attribute in _DOT_RE.findall(text)
        if alias in aliases and attribute not in aliases[alias] and attribute not in _BUILTIN_ATTRIBUTES
    })
    if bad_attributes:
        problems.append(f"unknown attribute(s) {bad_attributes}")
    return problems


def graph_query_tool(
    client: GraphDataMcpClient, *, timeout_seconds: float = 30.0, max_result_chars: int = 20_000
) -> ToolSpec:
    cached: list[_Schema] = []

    async def schema() -> _Schema:
        if not cached:
            data = await client.call_data(
                Tool.GET_GRAPH_SCHEMA.value, {"graph_name": client.graph_name}
            )
            cached.append(_Schema(data["schema"]))
        return cached[0]

    async def run_graph_query(params: RunGraphQueryInput) -> InvestigationResult:
        problems = check_query(params.query, await schema())
        if problems:
            return failure("the query was rejected before running", "; ".join(problems))

        wrapped = f"INTERPRET QUERY () FOR GRAPH {client.graph_name} {{\n{params.query}\n}}"
        try:
            result = await asyncio.wait_for(
                client.call(Tool.RUN_QUERY.value, {"graph_name": client.graph_name, "query_text": wrapped}),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return failure("the query timed out", f"no result within {timeout_seconds:g}s; narrow it down")
        if not result["ok"]:
            return failure("TigerGraph rejected or failed the query", result["error"] or "unknown error")

        printed = (result["data"] or {}).get("result")
        evidence = collect_evidence(printed, {})[:200]
        rendered = json.dumps(printed, default=str)
        if len(rendered) > max_result_chars:
            data: Any = {"truncated": True, "preview": rendered[:max_result_chars]}
            summary = f"query ran; result truncated to {max_result_chars} characters, narrow it down"
        else:
            data = printed
            summary = "query ran"
        return InvestigationResult(
            ok=True, summary=summary, data=data, error=None, evidence=evidence, citations=[]
        )

    return ToolSpec(
        "run_graph_query",
        "Run your own read-only GSQL against the fraud graph when no other tool answers the "
        "question. Write only the query body (no INTERPRET QUERY header); it is checked for "
        "read-only use and against the schema before it runs, and errors come back for you to fix. "
        "Prefer the dedicated tools for mule candidates, fund-transfer chains, shared devices/IPs "
        "and structuring.",
        RunGraphQueryInput,
        run_graph_query,
    )
