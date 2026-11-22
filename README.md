# NEXUS

**An AI-powered Personal Knowledge Management System that answers from *your* knowledge — and proves it with citations.**

Status: **design phase.** Architecture is specified in [`docs/`](./docs). No application code yet.

---

## What it does

NEXUS has five responsibilities:

| # | Responsibility | In practice |
|---|---|---|
| 1 | **Capture** | Notes, PDFs, DOCX, Markdown, saved conversations |
| 2 | **Organize** | Collections, tags, metadata, honest processing status |
| 3 | **Understand** | Extraction, chunking, embeddings, summaries, entities |
| 4 | **Connect** | Entity and document relationships, surfaced as a graph |
| 5 | **Recall** | Natural-language Q&A over your own corpus, with source citations |

## The one rule everything else follows

> NEXUS never answers from a language model's general memory. Every claim comes from a retrieved passage of your data, cited down to the document and page.

That is why retrieval runs before every generation, why the server strips citation markers the model invents, why an unanswerable question is refused instead of guessed at, and why row-level security is on every table from the first migration — a retrieval bug that crosses users is a data breach, not a UI glitch.

## Stack

| Layer | Choice |
|---|---|
| Frontend | SvelteKit · Tailwind CSS · TypeScript → **Vercel** |
| Application | Python **FastAPI** (REST, JWT auth, RAG orchestration) → **Fly.io** |
| Background | Python worker + Postgres job queue (`SKIP LOCKED`) → **Fly.io** |
| Data | Supabase **PostgreSQL** + **pgvector** + RLS |
| Storage | Supabase **Storage** (private bucket, signed URLs) |
| Auth | **Supabase Auth** |
| AI | External embedding + LLM APIs, RAG with hybrid search |

## Architecture at a glance

```
Capture ──▶ Storage ──▶ [job queue] ──▶ Worker: extract → chunk → embed ──▶ pgvector
                                                                              │
Question ──▶ embed ──▶ hybrid search (vector + keyword, RRF) ─────────────────┘
                             │
                             ▼
                    context assembly ──▶ LLM ──▶ answer + resolved citations
```

## Documentation

| Doc | Contents |
|---|---|
| [ARCHITECTURE.md](./docs/ARCHITECTURE.md) | Layers, trust boundaries, request flows, invariants, ADRs |
| [DATA-MODEL.md](./docs/DATA-MODEL.md) | Every table, index, RLS policy, storage layout, migration order |
| [RAG-PIPELINE.md](./docs/RAG-PIPELINE.md) | Extraction, chunking, hybrid retrieval, the grounding contract, evaluation |
| [API-SPEC.md](./docs/API-SPEC.md) | Endpoints, payloads, errors, rate limits, contract tests |
| [DEPLOYMENT.md](./docs/DEPLOYMENT.md) | Supabase + Fly.io + Vercel setup, secrets, migrations, observability |
| [ROADMAP.md](./docs/ROADMAP.md) | Build order M0–M6 and what is deliberately deferred |

## Non-negotiables

1. **Service-role keys and AI API keys never reach the browser.** The frontend holds the anon key only.
2. **RLS enforces tenancy**, not application `WHERE` clauses alone.
3. **No answer without a source.** `citations: []` and `grounded: false` is a valid, honest response.
4. **No silent partial success.** A failed ingest says why; a slow one shows which stage it's on.
5. **Retrieved text is data, not instructions.** Prompt injection inside a user's file must not steer the system.

## Repository layout (planned)

```
docs/            architecture and specifications
frontend/        SvelteKit app            (M4)
backend/         FastAPI app + worker     (M2–M3)
supabase/        migrations, policies, seed (M1)
```
