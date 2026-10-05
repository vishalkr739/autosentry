"""What an answer may cite: only ids the tools returned during this investigation.

Answers cite inline in square brackets, graph entities by id
([account-000127, device-ring-0000]) and regulatory guidance as
[doc:<doc_id>]. The registry collects every id the tools handed back; an
answer citing anything else is citing something nothing showed it.
"""

import re
from dataclasses import dataclass, field

from ..tools.results import Citation, Evidence, InvestigationResult

_BRACKETS = re.compile(r"\[([^\[\]]{1,400})\]")
_ID = re.compile(r"^(?:doc:)?[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _looks_like_id(part: str) -> bool:
    """An entity id has a separator (account-000127, tx_1), is an IP, or is a doc citation."""
    return bool(_ID.match(part)) and (
        "-" in part or "_" in part or part.startswith("doc:") or bool(_IPV4.match(part))
    )


def cited_ids(answer: str) -> list[str]:
    """Ids cited in square brackets, in order, without duplicates.

    Bracketed text that isn't a list of ids (an aside, a footnote mark) is
    not a citation and is ignored.
    """
    seen: dict[str, None] = {}
    for group in _BRACKETS.findall(answer):
        parts = [p.strip() for p in group.split(",")]
        if parts and all(_looks_like_id(p) for p in parts):
            for part in parts:
                seen.setdefault(part, None)
    return list(seen)


@dataclass
class EvidenceRegistry:
    """Everything the tools returned in a thread, so a follow-up answer may
    cite what an earlier turn found without fetching it again."""

    evidence: dict[str, Evidence] = field(default_factory=dict)
    citations: dict[str, list[Citation]] = field(default_factory=dict)

    def to_state(self) -> dict:
        return {"evidence": dict(self.evidence), "citations": {k: list(v) for k, v in self.citations.items()}}

    @classmethod
    def from_state(cls, state: dict | None) -> "EvidenceRegistry":
        state = state or {}
        return cls(dict(state.get("evidence") or {}), {k: list(v) for k, v in (state.get("citations") or {}).items()})

    def add(self, result: InvestigationResult) -> None:
        for item in result.get("evidence", []):
            self.evidence.setdefault(item["id"], item)
        for citation in result.get("citations", []):
            self.citations.setdefault(citation["doc_id"], []).append(citation)

    def known(self, cited: str) -> bool:
        if cited.startswith("doc:"):
            return cited.removeprefix("doc:") in self.citations
        return cited in self.evidence

    def unbacked(self, answer: str) -> list[str]:
        return [c for c in cited_ids(answer) if not self.known(c)]

    def cited(self, answer: str) -> tuple[list[Evidence], list[dict]]:
        """The graph evidence and regulatory documents the answer actually cites."""
        evidence: list[Evidence] = []
        documents: list[dict] = []
        for cited in cited_ids(answer):
            if cited.startswith("doc:"):
                passages = self.citations.get(cited.removeprefix("doc:"))
                if passages:
                    first = passages[0]
                    documents.append({
                        "doc_id": first["doc_id"],
                        "title": first["title"],
                        "url": first["url"],
                        "passages": [{"chunk_id": p["chunk_id"], "text": p["text"]} for p in passages],
                    })
            elif cited in self.evidence:
                evidence.append(self.evidence[cited])
        return evidence, documents
