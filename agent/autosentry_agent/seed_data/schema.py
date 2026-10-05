"""The reference schema's vertex and edge types, read from its GSQL file.

The loader needs each edge type's endpoint vertex types (tigergraph-mcp's
add_edges takes them per call) and the attribute names each type allows,
so a generated attribute that isn't in the schema is caught before load
instead of being silently dropped by TigerGraph.
"""

import re
from dataclasses import dataclass
from pathlib import Path

REFERENCE_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "reference_schema.gsql"

_VERTEX_RE = re.compile(r"^CREATE VERTEX (\w+) \(PRIMARY_ID (\w+) \w+(.*)\)\s*$")
_EDGE_RE = re.compile(r"^CREATE (?:DIRECTED|UNDIRECTED) EDGE (\w+) \(FROM (\w+), TO (\w+)(.*?)\)")
_ATTRIBUTE_RE = re.compile(r",\s*(\w+)\s+\w+")


@dataclass(frozen=True)
class VertexType:
    name: str
    primary_id: str
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class EdgeType:
    name: str
    from_type: str
    to_type: str
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceSchema:
    graph_name: str
    vertices: dict[str, VertexType]
    edges: dict[str, EdgeType]


def load_reference_schema(path: Path = REFERENCE_SCHEMA_PATH) -> ReferenceSchema:
    vertices: dict[str, VertexType] = {}
    edges: dict[str, EdgeType] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if match := _VERTEX_RE.match(line):
            name, primary_id, rest = match.groups()
            vertices[name] = VertexType(name, primary_id, tuple(_ATTRIBUTE_RE.findall(rest)))
        elif match := _EDGE_RE.match(line):
            name, from_type, to_type, rest = match.groups()
            edges[name] = EdgeType(name, from_type, to_type, tuple(_ATTRIBUTE_RE.findall(rest)))
    graph = re.search(r"CREATE GRAPH (\w+)", text)
    if not vertices or not edges or graph is None:
        raise ValueError(f"{path} does not look like the reference schema")
    return ReferenceSchema(graph.group(1), vertices, edges)
