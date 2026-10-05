"""python -m autosentry_agent.seed_data load --seed 42 --scale small [--reset]

Loads the deterministic seed graph into $TG_GRAPHNAME (the reference
schema's AutosentrySandbox) through the tigergraph-mcp at
$GRAPH_DATA_MCP_URL, then verifies it by counts. --ground-truth writes the
generator's ground truth (which accounts are mules, each ring's path) to a
JSON file for evals; it is never loaded into the graph.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from ..config import GraphDataSettings, get_secrets_provider
from ..mcp.transport import GraphDataMcpClient, GraphDataToolError
from .config import SCALE_PRESETS
from .generator import generate
from .loader import SeedLoadError, load_seed
from .structural_assertions import assert_structural_invariants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autosentry_agent.seed_data")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help="generate the seed graph and load it")
    load.add_argument("--seed", type=int, default=42)
    load.add_argument("--scale", choices=sorted(SCALE_PRESETS), default="small")
    load.add_argument("--reset", action="store_true", help="clear the graph's data first (destructive)")
    load.add_argument("--ground-truth", type=Path, help="write the ground truth JSON here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = generate(seed=args.seed, scale=args.scale)
    assert_structural_invariants(result)
    if args.ground_truth:
        args.ground_truth.write_text(json.dumps(result.ground_truth, indent=2, sort_keys=True), encoding="utf-8")

    client = GraphDataMcpClient(GraphDataSettings(), get_secrets_provider())
    try:
        report = asyncio.run(load_seed(client, result, reset=args.reset))
    except (SeedLoadError, GraphDataToolError) as exc:
        print(f"seed load failed: {exc}", file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
