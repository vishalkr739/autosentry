# External dependency pins, verified

- `tigergraph-mcp`: RESOLVED. Autosentry reaches it over **streamable
  HTTP** as its own service, authenticating each MCP session with `X-TG-*`
  headers, the same way savanna-agent does. This supersedes the original
  stdio-subprocess decision (spec section 7, item 6) so the two agents can
  converge; `1.0.3` already serves `--transport streamable-http` and reads
  the headers, verified live against a TigerGraph Cloud workspace.
  Autosentry talks to it with the `mcp` SDK directly (`mcp = "^1.30.0"`),
  not `langchain-mcp-adapters`. Verified via `pip install tigergraph-mcp
  --dry-run` and `pip index versions tigergraph-mcp`: the package **is**
  published on PyPI (this corrects the spec's original finding that it
  wasn't), currently at `1.0.3` (`1.0.0`-`1.0.3` all published). Pin:
  `tigergraph-mcp = "^1.0.3"`. No conda channel or vendored git SHA
  needed.
- TigerGraph Community Edition image: `tigergraph/tigergraph:4.2.5`

Re-verify both before bumping either in a future phase.
