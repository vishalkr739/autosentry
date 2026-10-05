"""Load a generated seed graph into a TigerGraph sandbox through tigergraph-mcp.

Uses add_nodes/add_edges (REST++ upserts that bind values to attributes by
name), not loading jobs: tigergraph-mcp 1.0.3's loading-job GSQL binds
VALUES() by position and ignores the column names given, which savanna-agent
found can silently load data into the wrong columns. Upserts also make a
re-load of the same seed idempotent.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tigergraph_mcp.tool_names import TigerGraphToolName as Tool

from ..mcp.transport import GraphDataMcpClient
from ..schema_tool.deploy import compute_schema_hash, deploy_reference_schema
from .generator import GenerationResult
from .schema import REFERENCE_SCHEMA_PATH, ReferenceSchema, load_reference_schema

logger = logging.getLogger(__name__)

_EDGE_KEYS = {"from_id", "to_id", "edge_type"}


class SeedLoadError(RuntimeError):
    """The seed data doesn't fit the schema, or the graph doesn't hold what was loaded."""


@dataclass
class LoadReport:
    graph: str
    deployed_schema: bool
    reset: bool
    vertex_counts: dict[str, int] = field(default_factory=dict)
    edge_counts: dict[str, int] = field(default_factory=dict)
    schema_hash: str = ""
    data_hash: str = ""

    def render(self) -> str:
        lines = [
            f"graph {self.graph}: schema {'deployed' if self.deployed_schema else 'already present'}"
            f"{', data reset first' if self.reset else ''}",
            "vertices: " + ", ".join(f"{t}={n}" for t, n in sorted(self.vertex_counts.items())),
            "edges:    " + ", ".join(f"{t}={n}" for t, n in sorted(self.edge_counts.items())),
            f"schema hash {self.schema_hash}",
            f"data hash   {self.data_hash}",
        ]
        return "\n".join(lines)


def data_hash(result: GenerationResult) -> str:
    """Sha256 of the generated data, the seed graph's identity."""
    return hashlib.sha256(json.dumps(result.to_dict(), sort_keys=True).encode()).hexdigest()


def plan_load(
    result: GenerationResult, schema: ReferenceSchema
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Group the data into add_nodes/add_edges payloads per type, refusing
    anything the schema doesn't declare instead of letting TigerGraph drop it."""
    problems: list[str] = []
    vertices: dict[str, list[dict]] = defaultdict(list)
    for entity in result.entities:
        vertex_type = schema.vertices.get(entity["type"])
        if vertex_type is None:
            problems.append(f"{entity['id']}: {entity['type']} is not a vertex type")
            continue
        attributes = {k: v for k, v in entity.items() if k not in ("id", "type")}
        unknown = set(attributes) - set(vertex_type.attributes)
        if unknown:
            problems.append(f"{entity['id']}: {sorted(unknown)} not attributes of {vertex_type.name}")
        vertices[vertex_type.name].append({"id": entity["id"], **attributes})

    edges: dict[str, list[dict]] = defaultdict(list)
    for edge in result.edges:
        edge_type = schema.edges.get(edge["edge_type"])
        if edge_type is None:
            problems.append(f"{edge['from_id']}->{edge['to_id']}: {edge['edge_type']} is not an edge type")
            continue
        attributes = {k: v for k, v in edge.items() if k not in _EDGE_KEYS}
        unknown = set(attributes) - set(edge_type.attributes)
        if unknown:
            problems.append(f"{edge_type.name} edge: {sorted(unknown)} not attributes of it")
        edges[edge_type.name].append({
            "source_type": edge_type.from_type,
            "source_id": edge["from_id"],
            "target_type": edge_type.to_type,
            "target_id": edge["to_id"],
            **attributes,
        })

    if problems:
        shown = "; ".join(problems[:10])
        raise SeedLoadError(f"{len(problems)} schema problem(s) in the seed data: {shown}")
    return dict(vertices), dict(edges)


async def load_seed(
    client: GraphDataMcpClient,
    result: GenerationResult,
    *,
    reset: bool = False,
    batch_size: int = 500,
    schema: ReferenceSchema | None = None,
    verify_timeout: float = 180.0,
    verify_interval: float = 3.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> LoadReport:
    """Make `client.graph_name` hold exactly `result`, and prove it by counts.

    Deploys the reference schema only if the graph doesn't exist. `reset`
    clears the graph's existing data first; it is destructive and never
    implied. Without it, a load upserts, so loading the same seed again is a
    no-op, while leftovers from a different seed fail the count check.
    """
    schema = schema or load_reference_schema()
    graph = client.graph_name
    if graph != schema.graph_name:
        raise SeedLoadError(
            f"the reference schema creates {schema.graph_name}, but the client targets {graph}"
        )
    vertices, edges = plan_load(result, schema)
    report = LoadReport(graph=graph, deployed_schema=False, reset=reset, data_hash=data_hash(result))

    async with client.connected():
        listed = await client.call_data(Tool.LIST_GRAPHS.value, {})
        if graph not in listed.get("graphs", []):
            logger.info("deploying the reference schema into new graph %s", graph)
            await deploy_reference_schema(client, str(REFERENCE_SCHEMA_PATH))
            report.deployed_schema = True
        elif reset:
            logger.warning("clearing all data in %s before loading", graph)
            await client.call_data(Tool.CLEAR_GRAPH_DATA.value, {"graph_name": graph, "confirm": True})
            # TigerGraph applies the deletion asynchronously; loading while
            # it is still being applied could lose freshly upserted data.
            await _wait_until_empty(client, graph, vertices, clock() + verify_timeout, clock, sleep, verify_interval)

        for vertex_type, rows in vertices.items():
            for start in range(0, len(rows), batch_size):
                await client.call_data(
                    Tool.ADD_NODES.value,
                    {
                        "graph_name": graph,
                        "vertex_type": vertex_type,
                        "vertices": rows[start:start + batch_size],
                        "vertex_id": "id",
                    },
                )
            logger.info("loaded %d %s", len(rows), vertex_type)
        for edge_type, rows in edges.items():
            for start in range(0, len(rows), batch_size):
                await client.call_data(
                    Tool.ADD_EDGES.value,
                    {"graph_name": graph, "edge_type": edge_type, "edges": rows[start:start + batch_size]},
                )
            logger.info("loaded %d %s edges", len(rows), edge_type)

        # TigerGraph's count statistics trail a fresh bulk upsert by seconds,
        # so the counts are re-read until they match or stop being plausible.
        deadline = clock() + verify_timeout
        while True:
            mismatches = await _count_mismatches(client, graph, vertices, edges, report)
            if not mismatches or clock() >= deadline:
                break
            logger.info("counts not settled yet (%d types behind), re-checking", len(mismatches))
            await sleep(verify_interval)
        report.schema_hash = await compute_schema_hash(client)

    if mismatches:
        raise SeedLoadError(
            "the graph does not hold exactly the seed data (" + "; ".join(mismatches) + "). "
            "If it holds another seed's data, load again with reset."
        )
    return report


async def _wait_until_empty(
    client: GraphDataMcpClient,
    graph: str,
    vertices: dict[str, list[dict]],
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    interval: float,
) -> None:
    while True:
        remaining = {}
        for vertex_type in vertices:
            count = (await client.call_data(
                Tool.GET_VERTEX_COUNT.value, {"graph_name": graph, "vertex_type": vertex_type}
            ))["count"]
            if count:
                remaining[vertex_type] = count
        if not remaining:
            return
        if clock() >= deadline:
            raise SeedLoadError(f"{graph} still holds data after clearing it: {remaining}")
        logger.info("waiting for the clear to finish (%d types not empty yet)", len(remaining))
        await sleep(interval)


async def _count_mismatches(
    client: GraphDataMcpClient,
    graph: str,
    vertices: dict[str, list[dict]],
    edges: dict[str, list[dict]],
    report: LoadReport,
) -> list[str]:
    mismatches = []
    for vertex_type, rows in vertices.items():
        count = (await client.call_data(
            Tool.GET_VERTEX_COUNT.value, {"graph_name": graph, "vertex_type": vertex_type}
        ))["count"]
        report.vertex_counts[vertex_type] = count
        if count != len(rows):
            mismatches.append(f"{vertex_type}: expected {len(rows)}, graph has {count}")
    for edge_type, rows in edges.items():
        count = (await client.call_data(
            Tool.GET_EDGE_COUNT.value, {"graph_name": graph, "edge_type": edge_type}
        ))["count"]
        report.edge_counts[edge_type] = count
        if count != len(rows):
            mismatches.append(f"{edge_type}: expected {len(rows)}, graph has {count}")
    return mismatches
