"""How dangerous is a tool call? One table for every tool the agent can see.

Mirrors savanna-agent's `agent/tool_risk.py`: explicit sets first, verb
heuristics second, and anything unrecognised fails closed as a write. The
risk table is kept separate from any lane's allow-list: a lane decides what
it may call, this decides what calling it does.
"""

from enum import StrEnum


class Risk(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# Autosentry's own investigation tools. The installed queries and knowledge
# search only read; run_graph_query only reaches TigerGraph after its guard
# has rejected anything that writes.
AUTOSENTRY_READ_TOOLS = frozenset(
    {
        "find_mule_candidates",
        "trace_fund_transfer_chain",
        "find_shared_infrastructure",
        "find_structuring_pattern",
        "search_knowledge",
        "run_graph_query",
    }
)

# tigergraph-mcp tools, named as the server registers them. The destructive
# and write sets match savanna-agent's; `gsql` runs arbitrary GSQL and
# `run_query` runs unguarded interpreted GSQL, so neither is treated as a read.
_DESTRUCTIVE = frozenset(
    {
        "tigergraph__drop_graph",
        "tigergraph__clear_graph_data",
        "tigergraph__drop_vector_attribute",
        "tigergraph__drop_data_source",
        "tigergraph__drop_all_data_sources",
        "tigergraph__drop_loading_job",
        "tigergraph__drop_query",
        "tigergraph__gsql",
    }
)
_WRITE = frozenset(
    {
        "tigergraph__create_graph",
        "tigergraph__add_vector_attribute",
        "tigergraph__create_data_source",
        "tigergraph__update_data_source",
        "tigergraph__create_loading_job",
        "tigergraph__run_loading_job_with_file",
        "tigergraph__run_loading_job_with_data",
        "tigergraph__load_vectors_from_csv",
        "tigergraph__load_vectors_from_json",
        "tigergraph__upsert_vectors",
        "tigergraph__install_query",
        "tigergraph__update_schema",
        "tigergraph__update_query_description",
        "tigergraph__run_query",
    }
)
_DESTRUCTIVE_VERBS = ("drop", "delete", "destroy", "clear", "terminate", "remove", "purge")
_WRITE_VERBS = ("add", "create", "update", "upsert", "install", "load", "insert", "set")
_READ_VERBS = (
    "get", "list", "show", "has", "is", "search", "fetch", "preview", "validate",
    "discover", "run_installed_query",
)


def classify(tool: str) -> Risk:
    """The risk of calling `tool`; unknown tools are writes, never reads."""
    if tool in AUTOSENTRY_READ_TOOLS:
        return Risk.READ
    if tool in _DESTRUCTIVE:
        return Risk.DESTRUCTIVE
    if tool in _WRITE:
        return Risk.WRITE
    verb = tool.removeprefix("tigergraph__")
    if verb.startswith(_DESTRUCTIVE_VERBS):
        return Risk.DESTRUCTIVE
    if verb.startswith(_WRITE_VERBS):
        return Risk.WRITE
    if tool.startswith("tigergraph__") and verb.startswith(_READ_VERBS):
        return Risk.READ
    return Risk.WRITE
