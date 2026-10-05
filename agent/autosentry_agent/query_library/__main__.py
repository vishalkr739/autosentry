"""python -m autosentry_agent.query_library install

Creates and installs agent/queries/*.gsql in $TG_GRAPHNAME through the
tigergraph-mcp at $GRAPH_DATA_MCP_URL, then confirms each is installed.
"""

import argparse
import asyncio
import logging
import sys

from ..config import GraphDataSettings, get_secrets_provider
from ..mcp.transport import GraphDataMcpClient, GraphDataToolError
from . import QueryInstallError, install_queries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autosentry_agent.query_library")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="create and install every query")
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = GraphDataMcpClient(GraphDataSettings(), get_secrets_provider())
    try:
        names = asyncio.run(install_queries(client))
    except (QueryInstallError, GraphDataToolError) as exc:
        print(f"query install failed: {exc}", file=sys.stderr)
        return 1
    print("installed: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
