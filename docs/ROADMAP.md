# NEXUS — Roadmap & Build Order

Status: **design (spec phase)**

The build order is chosen so that the product's riskiest claim — *"it answers from your data and cites it"* — is proven before anything is polished. UI, graph, and multi-tenant billing all rest on retrieval working.

---

## M0 · Foundation (repo + skeleton)

**Done when:** `make dev` boots frontend, API, and worker against a local Supabase stack; `/health` is green; CI runs lint + typecheck on every PR.

- Monorepo layout: `frontend/` (SvelteKit, Tailwind, TS), `backend/` (FastAPI), `supabase/` (migrations + seed), `docs/`
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
- DOCX / TXT / MD extractors; `no_text_layer` failure path
- Tests: chunker golden files (same input ⇒ same output), page-offset correctness, idempotent reprocess

---

## M3 · Recall (the vertical slice, part 2 — the product's core)

**Done when:** asking a question returns an answer whose every citation points at a real chunk, and an unanswerable question is refused rather than answered.

- `/v1/search` (hybrid, RRF, `matched_arm`)
- `/v1/chat` + `/v1/chat/stream`: retrieval → assembly → generation → citation resolution → persistence
- Score floor / ungrounded path; fabricated-marker stripping
- Eval harness with answerable / unanswerable / adversarial sets, run in CI
- Tests: no-LLM-call assertion on the refusal path; cross-tenant retrieval isolation

If M3 slips, everything else waits. A beautiful app over an ungrounded model is exactly the product this design exists to avoid.

---

## M4 · Usable app

**Done when:** a person can sign up, drop in a PDF, and get a cited answer without touching a terminal.

- SvelteKit shell: auth (Supabase), protected routes, session refresh
- Documents page: upload with progress driven by real `processing_stage`, list, delete, retry
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
| Sharing / collaborative collections | First explicit request; needs a permissions model beyond owner-only |
| Workspaces / org tenancy | First team user — schema already carries `user_id`, so this is an additive `workspace_id` migration |
| OCR for scanned PDFs | First user with image-only PDFs |
| Audio/video ingestion | Requested; needs transcription pipeline |
| Browser extension / mobile capture | Capture friction shows up in usage data |
| Fine-tuning or local models | Only if eval shows provider models can't hit the quality bar |
| Redis cache | p95 retrieval > 300 ms |
| Read replicas | Sustained read saturation |

---

## Definition of done, per feature

A feature ships only when: it has tests for the happy path **and** the cross-tenant denial path; failures surface honestly in the UI instead of a permanent spinner; and any change to retrieval, chunking, or prompts comes with an eval-set result attached to the PR.
