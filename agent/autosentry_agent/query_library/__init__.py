"""The installed investigation queries (agent/queries/*.gsql) and their installer.

Each file is one `CREATE OR REPLACE QUERY` against the reference schema
(spec section 4.1.3). Installing compiles them inside TigerGraph, after
which the agent's tools call them by name through run_installed_query.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from tigergraph_mcp.tool_names import TigerGraphToolName as Tool

from ..mcp.transport import GraphDataMcpClient

logger = logging.getLogger(__name__)

QUERY_DIR = Path(__file__).resolve().parents[2] / "queries"

_HEADER_RE = re.compile(r"CREATE OR REPLACE QUERY (\w+)\s*\(.*?\)\s*FOR GRAPH (\w+)", re.DOTALL)


class QueryInstallError(RuntimeError):
    """A query could not be created or installed, or isn't installed afterwards."""


@dataclass(frozen=True)
class QueryDefinition:
    name: str
    graph: str
    text: str


def load_library(directory: Path = QUERY_DIR) -> list[QueryDefinition]:
    queries = []
    for path in sorted(directory.glob("*.gsql")):
        text = path.read_text(encoding="utf-8")
        match = _HEADER_RE.search(text)
        if match is None or match.group(1) != path.stem:
            raise QueryInstallError(f"{path.name} must define CREATE OR REPLACE QUERY {path.stem}(...)")
        queries.append(QueryDefinition(match.group(1), match.group(2), text))
    if not queries:
        raise QueryInstallError(f"no queries in {directory}")
    return queries


def installed_queries(ls_output: str) -> set[str]:
    """Query names GSQL's `LS` lists as installed (`- name(...) (installed ...)`)."""
    return set(re.findall(r"^\s*- (\w+)\(.*\(installed", ls_output, re.MULTILINE))


async def install_queries(
    client: GraphDataMcpClient, queries: list[QueryDefinition] | None = None
) -> list[str]:
    """Create every query, install them in one batch, and prove they're installed.

    One INSTALL for the whole batch, since TigerGraph compiles them together
    and a Cloud install takes a minute or more. tigergraph-mcp's gsql tool
    only recognises some failure texts, so success is read from `LS`, not
    from its success flag. Safe to re-run: CREATE OR REPLACE replaces.
    """
    queries = queries if queries is not None else load_library()
    graph = client.graph_name
    wrong = [q.name for q in queries if q.graph != graph]
    if wrong:
        raise QueryInstallError(f"{wrong} are written FOR GRAPH other than {graph}")

    async with client.connected():
        for query in queries:
            result = await client.call_data(
                Tool.GSQL.value, {"command": f"USE GRAPH {graph}\n{query.text}"}
            )
            logger.info("created %s: %s", query.name, (result or {}).get("result", "")[-200:])
        names = ", ".join(q.name for q in queries)
        install = await client.call_data(
            Tool.GSQL.value, {"command": f"USE GRAPH {graph}\nINSTALL QUERY {names}"}
        )
        listing = await client.call_data(Tool.GSQL.value, {"command": f"USE GRAPH {graph}\nLS"})

    installed = installed_queries((listing or {}).get("result", ""))
    missing = [q.name for q in queries if q.name not in installed]
    if missing:
        raise QueryInstallError(
            f"not installed after INSTALL QUERY: {missing}; GSQL said: "
            f"{(install or {}).get('result', '')[-1500:]!r}"
        )
    return [q.name for q in queries]


__all__ = [
    "QUERY_DIR",
    "QueryDefinition",
    "QueryInstallError",
    "install_queries",
    "installed_queries",
    "load_library",
]
