# NEXUS — Roadmap & Build Order

Status: **M0–M4 implemented and tested; M5 partly; M6 deferred.**

This document is the build order, not a status board. What each milestone's
"Done when" line requires is stated so it can be checked; the per-item reality
is summarised at the end of this file and in the README.

The build order is chosen so that the product's riskiest claim — *"it answers from your data and cites it"* — is proven before anything is polished. UI, graph, and multi-tenant billing all rest on retrieval working.

---

## M0 · Foundation (repo + skeleton)

**Done when:** `make dev` boots frontend, API, and worker against a local Supabase stack; `/health` is green; CI runs lint + typecheck on every PR.

- Monorepo layout: `apps/web` (SvelteKit, Tailwind, TS), `apps/api` (FastAPI + worker), `supabase/` (migrations + seed), `docs/`
- Env handling that fails loudly on a missing secret
- Dockerfile for the backend; `fly.toml` with `web` + `worker` process groups
- CI: ruff, mypy (or pyright), eslint, svelte-check, pytest

---

## M1 · Data & tenancy

**Done when:** RLS cross-tenant tests pass and any attempt by user A to read user B's document returns zero rows at the *database* level, with application code bypassed.

- Migrations: extensions, `profiles` + signup trigger, `documents`, `collections`, `tags`, `chunks` (+ HNSW/GIN), `jobs`
- RLS policies on every user-scoped table
- `hybrid_search` SQL function + `updated_at` triggers
- Seed script: 2 users, ~10 documents, realistic text
- Tests: policy matrix, cascade behaviour, job claim under concurrency (`SKIP LOCKED` with 2 workers)

This milestone is deliberately before any UI. The tenant boundary is the one thing that is expensive to retrofit.

---

## M2 · Ingest pipeline (the vertical slice, part 1)

**Done when:** a PDF uploaded through a throwaway script lands as `ready` with correct chunk counts, page offsets, and embeddings — and a re-run is idempotent.

- Signed upload URL + confirm endpoints
- Worker: claim → download → extract → normalize → chunk → embed → write chunks → flip status, in the documented order
- Retry/backoff, staleness sweep, dead-letter visibility
- DOCX / TXT / MD / HTML extractors; PyMuPDF for PDF; `no_text_layer` failure path
- Tests: chunker golden files (same input ⇒ same output), page-offset correctness, idempotent reprocess

---

## M3 · Recall (the vertical slice, part 2 — the product's core)

**Done when:** asking a question returns an answer whose every citation points at a real chunk, and an unanswerable question is refused rather than answered.

- `/api/v1/search` (hybrid, RRF, `matched_arm`)
- `/api/v1/workspaces/{id}/chat` + `/chat/stream`: retrieval → assembly → generation → citation resolution → persistence
- Score floor / ungrounded path; fabricated-marker stripping
- Eval harness with answerable / unanswerable / adversarial sets, run in CI
- Tests: no-LLM-call assertion on the refusal path; cross-tenant retrieval isolation

If M3 slips, everything else waits. A beautiful app over an ungrounded model is exactly the product this design exists to avoid.

---

## M4 · Usable app

**Done when:** a person can sign up, drop in a PDF, and get a cited answer without touching a terminal.

- SvelteKit shell: auth (Supabase), protected routes, session refresh
- Documents page: upload with progress driven by real `processing_stage` (extracting → normalizing → chunking → embedding), list, delete, retry
- Notes: create/edit with debounced autosave → reindex
- Search page: results with `matched_arm`, snippet highlighting, jump-to-source
- Chat page: streaming with sources-first UI, citation chips → source viewer with passage highlight
- Collections + tags; dashboard with honest counts

---

## M5 · Connect

**Done when:** the graph shows entities actually extracted from the corpus, and the user can merge duplicates.

- Entity extraction job + relation edges
- Entity detail page: description, mentions, sources
- Graph visualisation (1–2 hops, capped), entity merge/rename
- Save-answer-as-note (turns a cited answer into durable knowledge)

---

## M6 · Harden & launch

- Rate limiting, quotas, usage dashboard
- Sentry, structured logs, grounded-rate metric
- Backup restore drill performed and documented
- Accessibility + keyboard navigation pass; mobile-responsive layout
- Cold-start and p95 latency measured against the budgets in RAG-PIPELINE.md §6
- Privacy page, data export, account deletion (cascade verified end-to-end)

---

## Deliberately after launch

| Feature | Trigger to revisit |
|---|---|
| Sharing / collaborative collections | First explicit request. The data model already supports it: members with `viewer`/`member`/`admin` roles exist, so this is UI and invitation flow, not a migration |
| Email invitations | First time someone needs to bring in a colleague; requires a token table, expiry and delivery |
| Per-workspace billing / seats | First paid team. `usage_events` already records model spend per workspace |
| OCR for scanned PDFs | First user with image-only PDFs |
| Audio/video ingestion | Requested; needs transcription pipeline |
| Browser extension / mobile capture | Capture friction shows up in usage data |
| Fine-tuning or local models | Only if eval shows provider models can't hit the quality bar |
| Redis cache | p95 retrieval > 300 ms |
| Read replicas | Sustained read saturation |

---


---

## Milestone numbering, and the second vocabulary

GitHub milestones mirror this file exactly — M0 through M6, with the same names
and the same "done / partly" verdicts — and then continue with three that this
file never had:

| GitHub milestone | Where it came from |
|---|---|
| M0 Foundation · M1 Data & Tenancy · M2 Ingest · M3 Recall · M4 Usable App | this file |
| M5 Connect · M6 Harden & Launch | this file |
| **M7 Knowledge Graph Visualisation** | UI half of M5, split out so it can be scheduled separately from the data work |
| **M8 Learning Intelligence** | new. No foundation exists — see below |
| **M9 Production Deployment** | new. The deployment checks that gate a readiness claim |

An external review proposed a different ten-milestone scheme
(`M0 Foundation · M1 Authentication and Workspaces · M2 Dashboard and UI ·
M3 Notes and Collections · M4 Document Management · M5 AI Embeddings and Search ·
M6 RAG AI Assistant · M7 Knowledge Graph · M8 Learning Intelligence ·
M9 Production Deployment`).

**That scheme was not adopted**, and the reason is worth recording. Its M1–M6
describe work that is already finished and already tested — authentication,
workspaces, notes, collections, documents, embeddings, search and the RAG
assistant all exist. Creating milestones for them would have produced a board
where six of ten milestones were satisfied on the day they were created, which
makes the remaining four invisible. The repo's own numbering tracks the order the
work actually had to happen in, so it is the one that stays.

Two items from the proposal *are* new and were added as M7-M9 rather than
discarded. M8 in particular is not a refinement of anything: there is no
flashcard, quiz, reviewer or progress schema, no route, and no UI. It is filed as
an issue so the milestone list cannot imply otherwise.

## Where each milestone actually stands

Checked against the repository, not against intent. The "Done when" line above is
the criterion; this is the verdict.

| Milestone | State | What is missing |
|---|---|---|
| **M0** Foundation | Done, except CI locality | `.github/workflows/ci.yml` now runs lint, types, unit, db/RLS and build. It is new: before this, `docs/DEVELOPMENT.md` §6 described gates that did not exist, and the db suite silently skipped without `DATABASE_URL`. |
| **M1** Data & tenancy | Done | 20 tables, 71 policies, composite FKs. `supabase/tests/tenancy.sql` asserts isolation with the application bypassed. |
| **M2** Ingest pipeline | Done, except OCR | Five extractors, `no_text_layer` path, version-guarded writes. OCR is still out of scope, as designed. |
| **M3** Recall | Done, except the eval set | Grounding contract, refusal-without-LLM, `matched_arm`, SSE. `docs/RAG-PIPELINE.md` §5 describes an eval harness that has no populated question set — so "the answer is *good*", as opposed to *grounded*, remains unmeasured. |
| **M4** Usable app | Done | Auth, documents, notes, search, streaming chat with citation chips, collections, dashboard. |
| **M5** Connect | **Partly** | Extraction and graph reads exist. Entity **merge/rename** and save-answer-as-note do not: there is no endpoint for either. |
| **M6** Harden & launch | **Partly** | Rate limiting and structured logs are real. Sentry, backup-restore drill, accessibility pass, latency measurement against the §6 budgets, and data export/account deletion are not done. |

Two things this table exists to prevent:

1. Reading a "design phase" header and assuming nothing is built when most of it is.
2. Reading a finished checklist and assuming the unmeasured parts — retrieval *quality*, merge/rename, export — are done.

## Definition of done, per feature

A feature ships only when: it has tests for the happy path **and** the cross-tenant denial path; failures surface honestly in the UI instead of a permanent spinner; and any change to retrieval, chunking, or prompts comes with an eval-set result attached to the PR.
