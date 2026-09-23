# Corpus ingestion status

Blocked on Track A Epic A2 (graphrag ingestion pipeline: landing zone,
durable queue, structural/malware validation, per-document status model),
per `docs/superpowers/specs/2026-09-21-autosentry-phase-0-foundations-spec.md`
section 6. Recommended, not strictly required for sandbox-only ingestion:
Track A Epic A0's security fixes landing first (cross-tenant conversation
leak and related bugs).

What this repo owns and is not blocked on:
- graphrag stood up via its own Docker Compose deployment (added back to
  docker-compose.dev.yml once Tasks 5/6/7/9 are unblocked, Task 12).
- FATF/FinCEN/OFAC content curation (tracked separately; not a code
  artifact).

Status: blocked. Last checked: 2026-09-22.
