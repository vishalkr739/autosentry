"""What an investigation tool hands back to the agent.

The transport's `{ok, summary, data, error}` plus what an answer must be
grounded in: `evidence`, the graph vertices the result rests on (so the
agent can cite "account-000127", not just describe it), and `citations`,
the regulatory passages a knowledge search returned.
"""

from typing import Any, TypedDict


class Evidence(TypedDict):
    type: str
    id: str


class Citation(TypedDict):
    doc_id: str
    chunk_id: str
    title: str
    url: str
    text: str


class InvestigationResult(TypedDict):
    ok: bool
    summary: str
    data: Any
    error: str | None
    evidence: list[Evidence]
    citations: list[Citation]


def failure(summary: str, error: str) -> InvestigationResult:
    return InvestigationResult(ok=False, summary=summary, data=None, error=error, evidence=[], citations=[])


def collect_evidence(value: Any, fields: dict[str, str]) -> list[Evidence]:
    """Every vertex reference in a query result, deduplicated, in first-seen order.

    `fields` maps a printed field name to its vertex type, for tuple fields
    a query declares as VERTEX (printed as a bare id). Printed vertex sets
    carry their own type, as `{"v_id", "v_type"}`, and need no mapping.
    """
    seen: dict[tuple[str, str], None] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("v_id"), str) and isinstance(node.get("v_type"), str):
                seen.setdefault((node["v_type"], node["v_id"]), None)
            for key, child in node.items():
                vertex_type = fields.get(key)
                if vertex_type and isinstance(child, str) and child:
                    seen.setdefault((vertex_type, child), None)
                elif vertex_type and isinstance(child, list):
                    for item in child:
                        if isinstance(item, str) and item:
                            seen.setdefault((vertex_type, item), None)
                        else:
                            walk(item)
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return [Evidence(type=t, id=i) for t, i in seen]
