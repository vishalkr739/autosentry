# External dependency pins, verified

- `tigergraph-mcp`: RESOLVED. The MCP-transport architecture decision
  (spec section 7, item 6) is settled in favor of `tigergraph-mcp` over
  `stdio`, per the canonical spec. Verified via `pip install tigergraph-mcp
  --dry-run` and `pip index versions tigergraph-mcp`: the package **is**
  published on PyPI (this corrects the spec's original finding that it
  wasn't), currently at `1.0.3` (`1.0.0`-`1.0.3` all published). Pin:
  `tigergraph-mcp = "^1.0.3"`. No conda channel or vendored git SHA
  needed.
- TigerGraph Community Edition image: `tigergraph/tigergraph:4.2.5`

Re-verify both before bumping either in a future phase.
