# NEXUS — Deployment & Development

Status: **implemented in the scaffold.** This describes how the three deployables are built, configured, and run — and the local loop to work on them.

---

## 1. Topology

```mermaid
flowchart LR
  subgraph V["Vercel · global edge"]
    FE["apps/web<br/>SvelteKit + Tailwind"]
  end
  subgraph F["Fly.io · same region as Supabase"]
    P1["nexus-api · process web<br/>uvicorn, min 1 machine"]
    P2["nexus-api · process worker<br/>1 machine, never scales to zero"]
  end
  subgraph S["Supabase"]
    PG[("Postgres + pgvector + RLS")]
    ST[("Storage: nexus-documents (private)")]
    AU["Auth"]
  end
  EXT["Embedding + LLM providers"]

  FE -->|Bearer JWT| P1
  FE -->|auth + signed uploads| S
  P1 --> PG
  P1 --> EXT
  P2 --> PG
  P2 --> ST
  P2 --> EXT
```

Three deployables, one database, one image for both backend processes.

---

## 2. Local development

### Prerequisites

`python3.11+`, `node 20+`, `docker` (for the Supabase CLI stack), and the [Supabase CLI](https://supabase.com/docs/guides/cli).

### First run

```bash
cp .env.example .env          # fill in SUPABASE_* after `supabase start`
make install                  # backend venv + npm install
supabase start                # local Postgres, Auth, Storage (docker)
make db-reset                 # apply migrations 0001–0011 + seed.sql
make dev                      # API :8000 + web :5173
```

`make db-reset` is the reset button: migrations are deterministic, so a clean database is always one command away.

### What the seed gives you

Three users, each with their own **personal workspace**:

| User | Password | Setup |
|---|---|---|
| `alice@nexus.local` | `password123` | Collection, two notes, one PDF record with chunks, a tag, a cited conversation, a queued job |
| `bob@nexus.local` | `password123` | One note whose wording is **near-identical** to Alice's, in a separate workspace |
| `carol@nexus.local` | `password123` | Invited into Alice's workspace as a **viewer** |

Bob's vocabulary is deliberately close to Alice's about deployment secrets. If his note ever appears in Alice's results, isolation is broken and you can see it by eye rather than having to write a test. Carol exists to exercise the role ladder: she can read Alice's workspace and cannot write to it.

Seed embeddings are deterministic pseudo-vectors (`pg_temp.pseudo_embedding`), not model output — enough to exercise the vector arm offline. `seed.sql` is idempotent: running it twice leaves identical data, which is what `make test-db` relies on.

### Commands

| Command | Does |
|---|---|
| `make dev` | API + web together |
| `make api` / `make worker` / `make web` | One at a time |
| `make test` | pytest (unit; DB tests skip without `DATABASE_URL`) |
| `make test-db` | Schema, RLS, and retrieval tests against a live database |
| `make lint` / `make fmt` | ruff + mypy + svelte-check |
| `make db-new name=add_thing` | New timestamped migration |
| `make db-lint` | Supabase linter |

### Running database tests locally

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres make test-db
```

These assert the things that must never regress:

* a non-member's workspace id is a `404` at the route and zero rows at the SQL layer;
* a `viewer` can read but every write policy refuses the statement;
* the LLM is not called when retrieval is empty;
* a fabricated citation marker persists no `message_sources` row;
* a citation's `source_kind` matches the parent column it populated.

The raw-SQL isolation suite is separate, and readable as a checklist:

```bash
psql "$DATABASE_URL" -f supabase/tests/tenancy.sql
```

It uses its own `aaaa…`/`bbbb…`/`cccc…` UUID space and is idempotent, so it is safe to run before
or after `seed.sql`. Every line says what it checked; a `SECURITY FAILURE` notice means a policy
has been loosened.

---

## 3. Supabase

1. **Pick the region once.** It cannot be changed after project creation, and every backend round trip pays for a wrong choice. For a user in the Philippines, `ap-southeast-1` (Singapore); put Fly machines in `sin` to match.
2. Run migrations: `supabase link --project-ref <ref> && make migrate-remote`.
3. Storage: bucket `nexus-documents` is created **private** by migration `0009`, with the size limit and mime allowlist applied there — not clicked together in the dashboard, so it is reviewable.
4. Auth: enable email + Google. Redirect allow-list must include the Vercel production domain **and** `http://localhost:5173`.
5. Copy project URL, anon key, service-role key, JWT secret, and the direct connection string.

### Migrations

Forward-only, numbered, and each one is safe to read in isolation:

| # | Contents |
|---|---|
| 0001 | extensions, `set_updated_at()`, `profiles` + signup trigger |
| 0002 | `collections`, `documents`, `tags`, `document_tags` |
| 0003 | `chunks` + HNSW + GIN, `embedding_dim()`, `assert_embedding_dim()` |
| 0004 | `entities`, `chunk_entities`, `entity_edges` |
| 0005 | `conversations`, `messages`, `message_citations` |
| 0006 | `jobs` (queue), `usage_events`, `enqueue_job()` |
| 0007 | RLS enablement + 48 policies (one auditable diff) |
| 0008 | `hybrid_search()` — RRF fusion + similarity gate |
| 0009 | storage bucket + path policies |
| 0010 | `pg_trgm` indexes (isolated: the one extension that can be skipped) |
| 0011 | explicit grants |

Use `CREATE INDEX CONCURRENTLY` for any index added to a table that already has rows in production.

---

## 4. Fly.io

One app, **two process groups** — same image, different commands. This makes deploys atomic: the API and worker can never run mismatched code.

```toml
# fly.toml
app = "nexus-api"
primary_region = "sin"

[build]
  dockerfile = "Dockerfile"

[env]
  ENVIRONMENT = "production"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 1
  processes = ["web"]

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"

[processes]
  web    = "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"
  worker = "python -m app.workers.main"
```

- **`worker` never scales to zero.** It is what makes uploads progress. `auto_stop_machines` applies to `web` only.
- `web` keeps one warm machine so the first chat after idle is not a 3-second cold start.
- Deploy with `fly deploy` (both groups), logs with `fly logs`.

```bash
fly secrets set \
  SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_JWT_SECRET=... DATABASE_URL=... OPENAI_API_KEY=... \
  CORS_ALLOWED_ORIGINS=https://nexus.vercel.app
```

---

## 5. Vercel

- Root directory: `apps/web`. Framework: SvelteKit (`@sveltejs/adapter-vercel`, node 20).
- Environment (Production **and** Preview scopes separately):

| Variable | Value |
|---|---|
| `PUBLIC_SUPABASE_URL` | project URL |
| `PUBLIC_SUPABASE_ANON_KEY` | anon key |
| `PUBLIC_API_BASE_URL` | `https://nexus-api.fly.dev` |

- **A service-role key, AI key, or `DATABASE_URL` in Vercel is a security bug**, not a configuration choice. `apps/web/src/lib/utils/env.ts` only reads `PUBLIC_*` names.
- CORS on the API must list the Vercel domains explicitly (`CORS_ALLOWED_ORIGINS`); never `*` with credentials.

---

## 6. CI

```mermaid
flowchart LR
  PR[PR] --> L[lint + typecheck]
  L --> T[pytest unit]
  T --> D[pytest db<br/>migrations + RLS + retrieval]
  D -->|merge| S{Split}
  S -->|apps/web| V[Vercel]
  S -->|apps/api| M[migrate] --> F[Fly deploy]
```

Blocking gates: lint, typecheck, unit tests, DB/RLS tests, and an eval-set run for any change touching chunking, retrieval, or prompts.

---

## 7. Observability

| Need | Now | Later |
|---|---|---|
| Logs | JSON to stdout with `request_id` per request | Ship to a sink |
| Errors | Sentry (`SENTRY_DSN`) | — |
| Health | `GET /health`, worker heartbeat in logs | Prometheus/Grafana |
| Slow queries | `pg_stat_statements` | Supabase insights |
| RAG quality | `grounding_violation` logs, `retrieval_debug`, message feedback | Dashboards |

**The one metric to build first:** grounded-answer rate — the share of turns with `grounded: true` and ≥1 citation. It is the product promise as a number. Prometheus, Grafana, and Redis are deferred (ADR-007) until there is traffic to graph.

---

## 8. Backups

Daily Supabase backups; PITR on a paid tier once the data is irreplaceable. Run a restore drill before launch: restore to a scratch project, point a Fly preview at it, confirm a chat answer still cites correctly. An untested backup is a rumour.

---

## 9. Configuration reference

| Variable | API | Worker | Web | Default |
|---|---|---|---|---|
| `ENVIRONMENT` | ✅ | ✅ | — | `local` |
| `SUPABASE_URL` | ✅ | ✅ | — | — |
| `SUPABASE_ANON_KEY` | ✅ | — | — | — |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | ✅ | ❌ | — |
| `SUPABASE_JWT_SECRET` | ✅ | — | — | — |
| `DATABASE_URL` | ✅ | ✅ | ❌ | — |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | ✅ | ✅ | ❌ | — |
| `PINNED_EMBEDDING_MODEL` / `_DIM` | ✅ | ✅ | — | `text-embedding-3-small` / 1536 |
| `LLM_MODEL` | ✅ | ✅ | — | `gpt-4o-mini` |
| `RETRIEVAL_K` / `CANDIDATE_K` | ✅ | — | — | 8 / 40 |
| `MIN_SIMILARITY` | ✅ | — | — | 0.25 |
| `SCORE_FLOOR` | ✅ | — | — | 0.008 |
| `VECTOR_WEIGHT` / `KEYWORD_WEIGHT` / `RRF_K` | ✅ | — | — | 1.0 / 0.7 / 60 |
| `MAX_UPLOAD_BYTES` | ✅ | — | — | 50 MB |
| `CORS_ALLOWED_ORIGINS` | ✅ | — | — | localhost:5173 |
| `WORKER_CONCURRENCY` | — | ✅ | — | 2 |
| `PUBLIC_*` | — | — | ✅ | — |

`config.py` rejects a `PINNED_EMBEDDING_DIM` that disagrees with the pinned model — a mismatch produces vectors that look fine and retrieve nothing.
