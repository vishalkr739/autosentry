# Autosentry

Monorepo: `agent/` (Python/Poetry, LangGraph agent backend) and `web/`
(Vite/React/TypeScript frontend). See `docs/superpowers/specs/` for the
design spec (gitignored; ask a maintainer for the current copy if you
don't have one).

## Running the investigator locally

1. Copy `.env.example` to `.env` and fill in `TG_HOST`, `TG_JWT_TOKEN` and
   `LLM_API_KEY`. Knowledge search also needs graphrag running from its own
   repo (`GRAPHRAG_BASE_URL`).
2. `docker compose -f docker-compose.dev.yml up -d` starts Postgres,
   tigergraph-mcp (port 8010) and the agent (port 8000).
3. Seed the sandbox graph and install the investigation queries (from
   `agent/`, with the same environment):

   ```
   poetry run python -m autosentry_agent.seed_data load --seed 42 --scale small
   poetry run python -m autosentry_agent.query_library install
   ```

4. Ask a question:

   ```
   curl -s localhost:8000/investigate -H 'content-type: application/json' \
     -d '{"question": "List mule accounts in the graph"}'
   ```

   `/investigate/stream` takes the same body and streams NDJSON events
   (`turn_start`, `activity`, `message`, `final`, `error`). Pass the returned
   `thread_id` to ask a follow-up in the same conversation.

**The API has no authentication yet.** Run it only locally or for demos,
never exposed on a network.
