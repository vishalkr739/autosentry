# Autosentry: Project Context Index

Autosentry is an agentic fraud-investigation system for banks, built on
TigerGraph's graph database and the `graphrag` retrieval engine. This file
indexes the design documents in `docs/superpowers/specs/` by role, so a
build session pulls the right doc for the task instead of guessing from
filenames or dates.

## Read first

**`2026-09-04-autosentry-full-technical-specification.md`** is the
canonical, self-contained architecture spec. It restates the system from
first principles and does not require cross-referencing anything else.
When any two documents disagree on architecture, this one wins.

## The rest of the doc set, by role

| File | Role | Use it for |
|---|---|---|
| `2026-09-04-autosentry-full-technical-specification.md` | **Canonical spec.** Self-contained. | Architecture, data model, agent design, API contracts, security, the phase roadmap. Start here for anything. |
| `2026-09-03-autosentry-production-build-design.md` | **Historical decision record.** Superseded by the full spec for architecture. | The original code-reading research findings (§3/§3.1: what was directly confirmed in `graphrag` and `tigergraph-mcp` source, plus the external reference-system review). Read only for that provenance, not for current architecture. |
| `2026-09-04-autosentry-onboarding-flow.md` | Focused companion to the full spec's §4.4 (schema mapping) and §4.3 (tenant boundary). | The step-by-step onboarding UX flow (admin enables Autosentry → schema discovery → mapping proposal → confirmation → compiled tools → first query). **Known drift**: written before Schema Mapping was formalized as its own independently-configured capability (separate from the four agents); see the note below. |
| `2026-09-04-autosentry-lifecycle-flow.md` | Small standalone asset. | A Mermaid recreation of the PRD's six-stage lifecycle diagram (`Autosentry flow.png`, PRD Appendix A). Use when you need that diagram as text/code instead of an image. |
| `2026-09-07-autosentry-prd-requirements-traceability.md` | Traceability matrix, companion to the full spec. | Checking that a specific PRD requirement (GR-, AS-, WF-, ADV-, SEC- numbered) has a corresponding design decision, and where. |
| `2026-09-08-autosentry-eraser-diagram-reference.md` | Companion to the live Eraser board (workspace `j35CFogT8fyV10dPAaO9`). | The plain-language "what is this / why / how" explanation behind every box in every diagram. **Known drift**: does not yet describe the schema-hash drift-check gate added to the "Orchestration Agent State Flow" diagram on 2026-09-15, or the Schema Mapping capability's own diagram treatment. |
| `2026-09-09-autosentry-unimplemented-requirements.md` | **Dependency audit**, not an Autosentry design doc. | What's missing or broken in `graphrag`/`tigergraph-mcp`/`cloud-universe` today, verified by reading their code. This is the evidence behind Track A of the ticket breakdown below. |
| `2026-09-09-autosentry-jira-ticket-breakdown.md` | **Derived planning artifact.** Not yet created in Jira. | The proposed epic/story breakdown (Track A: fix `graphrag`; Track B: build Autosentry). Sources: the unimplemented-requirements audit, the full spec's §14 roadmap, and the traceability matrix. **Known drift**: does not yet include the Schema Mapping capability's config story or the schema-drift-check guardrail story added 2026-09-15. |
| `2026-09-15-agents-in-autosentry-architecture-and-plan.md` | **External-facing mirror**, condensed to match the "Agents in Savanna" Confluence template. | A shorter, presentation-shaped version of the full spec, meant for the published Confluence page. **Known drift, important**: the live Confluence page (`Autosentry: Agentic Fraud Investigation Architecture`) has since been updated directly (the Schema Mapping section, the drift-check gate, 7 embedded diagrams) without those edits being written back into this local file. Treat the live Confluence page as current for anything published after local version 1.0; this file is behind it. |

## Precedence rules

1. For architecture: `full-technical-specification.md` beats every other local file.
2. For the presentation/Confluence-mirror doc specifically: the **live Confluence page** beats this local file, since edits have been made directly on Confluence that were never synced back locally.
3. For `graphrag`/`tigergraph-mcp` capability claims: verify against the unimplemented-requirements audit before trusting an older doc's assumption, since that audit is the most recently verified-by-reading-code source.
4. A "Known drift" note above means: don't build against that section of that file without checking whether the underlying decision has moved on since.

## Standing design decisions worth knowing before touching related code

- **Schema Mapping is its own independently-configured capability**, not folded into the Orchestration Agent and not a fifth persistent LangGraph node. It has its own `{endpoint, api_key, model, temperature}`, no `investigation_id`, no tool-calling loop, and runs only at onboarding and on confirmed schema drift.
- **Schema drift is checked twice**: a periodic background job (already in the onboarding-flow doc), plus a synchronous schema-hash check at the start of every investigation turn that fails closed and routes to the same admin re-confirmation flow used at onboarding, rather than letting a stale compiled query fail mid-investigation.

These two decisions are reflected in full on the live Confluence page and in the "Orchestration Agent State Flow" Eraser diagram, but not yet in the local onboarding-flow doc, the eraser-diagram-reference doc, the jira-ticket-breakdown doc, or this repo's local copy of the architecture-and-plan doc (see the drift notes above).
