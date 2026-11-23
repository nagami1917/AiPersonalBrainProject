# NEXUS — Deployment

Four services, three providers, one rule that shapes all of it: **the browser
only ever receives public keys.** Everything below exists to keep that true while
still letting the browser talk to the API.

```
        ┌─────────────────────┐
        │   User's browser    │
        │  desktop / mobile   │
        └──────────┬──────────┘
                   │  HTTPS — anon key + JWT only
        ┌──────────▼──────────┐
        │       Vercel        │   SvelteKit (adapter-vercel, node 20)
        │  SvelteKit frontend │
        └──────────┬──────────┘
                   │  HTTPS + `Authorization: Bearer <supabase jwt>`
        ┌──────────▼──────────┐
        │  Fly.io `web`       │   FastAPI (uvicorn, 2 workers)
        │  Fly.io `worker`    │   Postgres-backed job queue
        └────┬───────────┬────┘
             │           │
   ┌─────────▼──┐   ┌────▼─────────┐
   │  Supabase  │   │ AI provider  │
   │ Auth·PG·St │   │ LLM·Embed    │
   └────────────┘   └──────────────┘
```

The `web` and `worker` processes run **one image** on purpose: they can never be
deployed at different code versions (`fly.toml`, `[processes]`).

---

## 1. Prerequisites

| Thing | Version | Notes |
|---|---|---|
| Supabase project | any paid tier for PITR | note the **region** — Fly's `primary_region` must match |
| Fly.io app | 2 processes | one app, `web` + `worker` |
| Vercel project | SvelteKit detected | root directory `apps/web` |
| AI provider key | OpenAI-compatible | never leaves Fly |

---

## 2. Two environment files, deliberately

The frontend and backend read **different files with different rules**, and
conflating them is how a service-role key ends up in a bundle.

| File | Committed? | Holds secrets? | Who reads it |
|---|---|---|---|
| `apps/api/.env` | ❌ (gitignored) | ✅ yes | FastAPI + worker process |
| `apps/web/.env.local` | ❌ (gitignored) | ❌ no — `PUBLIC_*` only | Vite at build time, inlined into the bundle |
| `apps/api/.env.example` | ✅ | blank values | humans |
| `apps/web/.env.example` | ✅ | blank values | humans |

`make install` creates both from their templates if absent. To start from scratch
by hand:

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

`.gitignore` covers `.env` and `.env.*` while keeping every `**/.env.example`
trackable, so a template is always in the repository and a real value never is.
Verify with:

```bash
git check-ignore -v apps/api/.env          # → ignored
git check-ignore -v apps/api/.env.example  # → not ignored
```

### 2.1 The variables

**Backend** (`apps/api/.env`, and Fly secrets in production):

```bash
SUPABASE_URL=                        # REQUIRED
SUPABASE_ANON_KEY=                   # REQUIRED
SUPABASE_SERVICE_ROLE_KEY=           # REQUIRED — bypasses RLS, server only
SUPABASE_JWT_SECRET=                 # REQUIRED, 32+ chars
DATABASE_URL=                        # REQUIRED (or SUPABASE_DB_URL)
OPENAI_API_KEY=                      # REQUIRED (or AI_API_KEY)
LLM_MODEL=gpt-4o-mini
PINNED_EMBEDDING_MODEL=text-embedding-3-small
PINNED_EMBEDDING_DIM=1536
CORS_ALLOWED_ORIGINS=http://localhost:5173
MAX_UPLOAD_SIZE_MB=25
```

**Frontend** (`apps/web/.env.local`, and Vercel environment):

```bash
PUBLIC_SUPABASE_URL=
PUBLIC_SUPABASE_ANON_KEY=
PUBLIC_API_BASE_URL=                 # empty = same-origin /api/v1 (recommended)
```

### 2.2 Accepted aliases

The deployment document and this codebase name several settings differently.
Both spellings are read — canonical first — because a renamed secret in Fly or
Vercel otherwise fails at boot and looks like an outage rather than a typo.

| Spec name | Canonical name | Where it is resolved |
|---|---|---|
| `SUPABASE_DB_URL` | `DATABASE_URL` | `Settings` model validator |
| `AI_API_KEY` | `OPENAI_API_KEY` | `Settings` model validator |
| `AI_MODEL` | `LLM_MODEL` | `Settings` model validator |
| `EMBEDDING_MODEL` | `PINNED_EMBEDDING_MODEL` | `Settings` model validator |
| `ALLOWED_ORIGINS` | `CORS_ALLOWED_ORIGINS` | `Settings` model validator |
| `MAX_UPLOAD_SIZE_MB` | `MAX_UPLOAD_BYTES` | `Settings` model validator |

`API_BASE_URL` and `PUBLIC_API_BASE_URL` are **not** aliases of each other, and
this table used to say they were. They answer different questions:

| Variable | Question it answers | Visible to |
|---|---|---|
| `PUBLIC_API_BASE_URL` | what origin should the *browser* send `/api/v1/...` to? | the browser (it is `PUBLIC_`) |
| `API_BASE_URL` | where should the *server* forward `/api/v1/...`? | the Vercel function only |

Empty `PUBLIC_API_BASE_URL` plus a set `API_BASE_URL` is the default same-origin
shape (§3.1). A set `PUBLIC_API_BASE_URL` means the browser bypasses the proxy
entirely (§3.2).

`tests/unit/test_config_aliases.py` pins that a spec-only environment boots, that
the canonical name wins when both are set, and that an alias cannot introduce an
inconsistent embedding pair.

---

## 3. How the browser reaches the API

Two supported shapes. The default is the one that cannot be misconfigured.

### 3.1 Same origin (default)

`PUBLIC_API_BASE_URL` is **empty**. The client calls `/api/v1/...` on whatever
origin served the page, and one route forwards it to FastAPI:

```
src/routes/api/v1/[...path]/+server.ts     /api/v1/* → {API_BASE_URL}/api/v1/*
```

The path is forwarded **unchanged**. FastAPI mounts its routers at `/api/v1`
(`app/core/config.py:API_PREFIX`) and the browser calls `/api/v1`, so there is
no translation step to get wrong. An earlier design mounted FastAPI at `/v1`
and had this route strip the `/api` segment; that worked, but it meant every log
line, doc example and test URL had to know which side of the proxy it was on.

That route runs under `vite dev` **and** as a Vercel function, so development and
production use the same code path. Set `API_BASE_URL` to the FastAPI origin in
both:

| Environment | `API_BASE_URL` |
|---|---|
| Local | `http://127.0.0.1:8000` (the default when unset outside production) |
| Vercel | `https://nexus-api.fly.dev` — server-only, never `PUBLIC_` |

Why this is the default: a relative path is the only configuration that is
correct in *both* environments. The tempting `http://localhost:8000` works on the
developer's laptop and fails for every real user, because their browser cannot
reach the developer's `localhost`. `scripts/check-env.sh` fails a production
deploy that sets a localhost API URL.

Rules the route follows, so the proxy cannot become a hole in the architecture:

- It forwards `Authorization` untouched and **does not validate tokens**. FastAPI
  remains the only verifier; a proxy that also checked JWTs would be a second,
  weaker copy of the security boundary.
- It never touches Postgres and holds no service-role key, so it cannot bypass
  RLS on the way through.
- It relays the SSE body as a stream. A buffering proxy turns a token-by-token
  answer into one late blob that reads as a hang.
- In production with no `API_BASE_URL` it returns **503
  `api_upstream_not_configured`** with the fix in the message, rather than
  attempting `127.0.0.1` from a server where that can only fail as
  `ECONNREFUSED`.

> Worth knowing: this proxy *is* a genuinely new hop — request bytes traverse a
> Vercel function before reaching Fly. If that latency matters, use §3.2, where
> the browser calls FastAPI directly. Both shapes are supported; only the
> configuration differs.

### 3.2 Separate origin (absolute URL)

Set `PUBLIC_API_BASE_URL=https://nexus-api.fly.dev`, and add the frontend's
origin to the API's CORS list:

```bash
fly secrets set CORS_ALLOWED_ORIGINS=https://app.example.com,https://nexus-*.vercel.app
```

Rules that matter here:

- **Never `*`.** `allow_credentials=True` with a wildcard is rejected by browsers
  and is a genuine misconfiguration, not a shortcut. The API splits the value on
  commas.
- **Exact origins**, not patterns. `https://nexus-*.vercel.app` above is shown to
  make the point that preview deployments need their own entries — CORS does not
  support wildcards, so either list the preview domains or point previews at the
  same-origin shape.
- Both the spec's `API_BASE_URL` and `PUBLIC_API_BASE_URL` are read by the dev
  proxy, but **only the `PUBLIC_` name reaches the browser**: SvelteKit exposes
  `PUBLIC_*` and nothing else. That is the framework enforcing the architecture's
  first rule, so the app does not pretend otherwise (`check-env.sh` warns when
  only the bare name is set).

The client handles either spelling of the base URL, including the spec's
`http://host:8000/api/v1` form, without duplicating the prefix —
`src/lib/api/paths.ts`, covered by `paths.test.ts`.

---

## 4. Deploying each piece

### 4.1 Supabase

```bash
supabase link --project-ref <ref>
make migrate-remote          # applies supabase/migrations in order
```

Migrations are idempotent (`create table if not exists`,
`on conflict do update`), so re-running is safe and a partial failure is
recoverable. Verify the security posture after migrating:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/tenancy.sql
```

That script asserts workspace isolation as a non-superuser role; a migration that
silently dropped a policy fails here rather than in production.

Storage: the bucket is created by migration `0009` as **private** with a 50 MB
limit and a MIME allowlist. Do not change it to public.

### 4.2 Fly.io

```bash
fly secrets set \
  SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_JWT_SECRET=... DATABASE_URL=... OPENAI_API_KEY=... \
  CORS_ALLOWED_ORIGINS=https://app.example.com \
  MAX_UPLOAD_SIZE_MB=25

make deploy-api              # fly deploy
```

`fly.toml` declares the two process groups; the worker deliberately has no HTTP
service and must never scale to zero.

Use the **pooled** database connection string (Supabase's `:6543` port) for
`DATABASE_URL`. The direct port exhausts connections under `--workers 2` plus a
worker process.

### 4.3 Vercel

Root directory `apps/web`. Environment variables, set for **Production and
Preview separately** — a variable set only for Production leaves every preview
build blank:

| Variable | Value |
|---|---|
| `PUBLIC_SUPABASE_URL` | project URL |
| `PUBLIC_SUPABASE_ANON_KEY` | anon key |
| `PUBLIC_API_BASE_URL` | empty for the same-origin shape (§3.1), or `https://nexus-api.fly.dev` for §3.2 |
| `API_BASE_URL` | **`https://nexus-api.fly.dev`** — server-only upstream for the same-origin route |

The two API variables are not alternatives to each other. `PUBLIC_API_BASE_URL`
tells the *browser* where to send requests; `API_BASE_URL` tells the *Vercel
function* where to forward them. Setting only the first works (§3.2). Setting
only the second is the same-origin shape, which is the default. Setting neither
leaves a deployed app that cannot reach its API — and in production the proxy
says exactly that with a 503 instead of failing obscurely.

A service-role key, AI key, or `DATABASE_URL` in Vercel is a **security bug**, not
a configuration choice. `src/lib/utils/env.ts` reads only `PUBLIC_*` names, so
such a value could not be used anyway — but remove it rather than rely on that.

---

## 5. Pre-deploy check

```bash
make deploy-check            # or: ./scripts/check-env.sh
```

Reads the environment, prints PASS/FAIL per variable, and **never prints a
value**. It fails on the mistakes that are otherwise invisible until traffic:

- a missing or too-short JWT secret;
- an embedding model/dimension pair that cannot produce usable vectors;
- CORS containing a wildcard, or pointing at localhost in production;
- `PUBLIC_API_BASE_URL` pointing at localhost in production;
- a service-role key present in the frontend environment (`CHECK_FRONTEND=1`).

It reports which spelling satisfied each requirement, so an operator can see that
`SUPABASE_DB_URL` was picked up rather than guessing.

---

## 6. Verifying a deployment

```bash
curl -fsS https://nexus-api.fly.dev/health
curl -fsS https://nexus-api.fly.dev/api/v1/meta | jq .rate_limits
```

`/api/v1/meta` is the capability document: supported MIME types, upload limit,
embedding model, and the rate-limit budget. If the frontend renders but every
request fails, compare `/api/v1/meta` on the API's own domain with the same path
through the frontend's origin — a 404 on one and 200 on the other is a proxy or
base-URL mismatch, not an application error.

Then the one check that exercises the whole chain: sign in, upload a document,
wait for `ready`, and ask a question whose answer is only in that document. A
grounded answer with a citation means auth, RLS, storage, the worker, retrieval,
and the provider are all working.

---

## 7. Rollback

| Layer | How |
|---|---|
| Frontend | Vercel → Deployments → *Promote to Production* on the previous build |
| Backend | `fly releases` then `fly deploy --image <previous>` |
| Database | Supabase PITR (paid tier), or restore a logical backup |

Migrations are forward-only. A destructive change needs a two-step deploy
(add column, ship code that writes both, later drop the old one) — there is no
`down` to run.
