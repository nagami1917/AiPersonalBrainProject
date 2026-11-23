# NEXUS — Deployment Status

**As deployed on 2026-11-23.** This records what is actually live, what is not,
and the exact commands to finish the remaining step. It supersedes nothing in
[DEPLOYMENT.md](DEPLOYMENT.md) — that document explains *how* to deploy; this one
records *what happened* when we did.

> **Read this first:** the frontend and database are live. The API and worker are
> **not**, so the deployed app cannot complete a login. That is a deliberate,
> recorded gap (see §4), not a silent failure — the proxy returns an explicit
> `503 api_upstream_not_configured` rather than pretending to work.

---

## 1. What is live

| Piece | Target | Identifier | State |
|---|---|---|---|
| Frontend | Vercel, region `sin1` | `nexus` / `prj_6Tn8MppmKEaR4BMj78fNOYEQaJR6` | **READY**, production |
| Database | Supabase, `ap-southeast-1` | `tthaxwiootsbidryiboe` (`nexus`) | ACTIVE_HEALTHY |
| Storage | Supabase bucket | `nexus-documents` (private, 50 MiB cap) | created |
| API | Fly.io (`nexus-api`, `sin`) | — | **not deployed** |
| Worker | Fly.io (same image, `worker` process group) | — | **not deployed** |

Production URL: **https://nexus-final-thesis.vercel.app**

Deployment `dpl_EXjemDv9LBA4rPEgaQu3x6cJjEW3`, built from `a8787f4` on
`arena/0c074137-aipersonalbrainproject`. Vercel reports `ready_state: READY`,
`readySubstate: PROMOTED`, and `aliasAssigned: true` for the production alias.

## 2. Database

All **19 migrations** (`0001`–`0019`) and `seed.sql` were applied to
`tthaxwiootsbidryiboe`, in order, each under its own name so the migration
history matches the repository file names.

Resulting schema, verified by query on the live project:

| Measurement | Expected | Actual |
|---|---|---|
| Tables in `public` | 20 | **20** |
| RLS policies in `public` | 67 | **67** |
| Storage policies | 4 | **4** |
| Triggers (non-internal, `public`) | 10 | **10** |
| Extensions | `vector`, `pg_trgm`, `pgcrypto` | **all 3** |
| `embedding_dim()` | 1536 | **1536** |
| Storage bucket | `nexus-documents` | **present** |

Seed outcome — the `handle_new_user` trigger built the full tenancy graph for
each of the three demo users:

| Table | Rows |
|---|---|
| `auth.users` | 3 |
| `profiles` (with `default_workspace_id`) | 3 (3) |
| `workspaces` | 3 |
| `workspace_members` | 4 |
| `notes` / `documents` | 3 / 1 |
| `document_chunks` (with embedding) | 5 (5) |
| `conversations` / `chat_messages` / `message_sources` | 1 / 2 / 1 |
| `tags` / `processing_jobs` | 1 / 1 |

`0010_trigram_indexes.sql` **did** apply here. That migration is skipped in local
CI because the CI Postgres image lacks `pg_trgm`; Supabase ships it, so the
typo-tolerant indexes exist in production.

### Demo accounts

The seed creates three users, all with password `password123`:

| Email | Role in Alice's workspace |
|---|---|
| `alice@nexus.local` | owner |
| `bob@nexus.local` | — (own workspace) |
| `carol@nexus.local` | viewer |

`bob@nexus.local`'s workspace holds deliberately near-identical vocabulary to
Alice's, so tenant leakage is visible to the eye and not only to assertions.

> **These are development fixtures with a published password.** Before any real
> user is invited, delete them — see §6.

## 3. Frontend

The project was created with root directory `apps/web`, framework SvelteKit,
linked to `nagami1917/AiPersonalBrainProject` (`main`).

Environment variables, set for **Production, Preview and Development together** —
a variable set only for Production leaves every preview build blank:

| Variable | Sensitivity | Value |
|---|---|---|
| `PUBLIC_SUPABASE_URL` | public | `https://tthaxwiootsbidryiboe.supabase.co` |
| `PUBLIC_SUPABASE_ANON_KEY` | public (by design) | project anon key |
| `API_BASE_URL` | server-only | `https://nexus-api.fly.dev` |

No service-role key, AI key, or `DATABASE_URL` is present in Vercel. That is the
rule, not an oversight (`SECURITY.md` §2): anything in the Vercel bundle is
public by construction.

### A deployment-only bug, now fixed

The first production build **succeeded** but the deployment **failed** at
Vercel's sandbox stage:

```
NOW_SANDBOX_WORKER_RUNTIME_INVALID
You are using a custom Runtime that depends on nodejs20.x, which is discontinued.
```

`apps/web/svelte.config.js` pinned `adapter({ runtime: 'nodejs20.x' })`. The
adapter accepts any `nodejs<N>.x` for N ≥ 18, so the pin was an arbitrary choice
that has since expired. Fixed in `a8787f4` by pinning `nodejs22.x`, which also
matches what CI builds on. There is **no way to catch this locally**: a local
`npm run build` emits a valid Build Output bundle and exits 0 either way. Only a
real upload to Vercel rejects the runtime.

### Git deployments do work after all

An earlier note claimed the project was created with git deployments disabled and
that merging would not publish. **That was wrong** — checked against the live
deployment list. Merge commit `5961edd` produced a production deployment
(`dpl_GRg7R9sqvCHVSZaT82ideEeXcJxG`, `READY` / `PROMOTED`), and the platform also
built each push to the branch as a staged deployment.

So a push to `main` *does* publish to `https://nexus-final-thesis.vercel.app`.
The confusion came from the project's `link.gitProviderOptions.createDeployments`
reading `disabled` at creation time; what actually governs this is the
GitHub-App integration, which is active.

## 4. What is deliberately not done

### The API and worker are not deployed

`fly.toml` and the `Dockerfile` are ready and unchanged (`app = "nexus-api"`,
`primary_region = "sin"`, two process groups, the worker with no HTTP service).
What is missing is execution, for two independent reasons:

1. **No credentials.** There is no `FLY_API_TOKEN` in this environment.
2. **No network path.** `https://api.fly.io` is not reachable from the
   deployment environment (`HTTP 000`), while e.g. `api.github.com` returns 200.
   So this is not only a missing token — the fly.io control plane cannot be
   reached from here at all.

`fly.toml` sets `primary_region = "sin"`, which is correct: it matches the
Supabase project's `ap-southeast-1`.

### Consequence, stated plainly

The deployed frontend renders, and its `/api/v1/*` proxy answers
`503 api_upstream_not_configured` (or a connection error against the placeholder
`nexus-api.fly.dev`). **A login cannot complete end to end yet.** The app is not
"deployed and working"; it is *frontend and database deployed*.

## 5. Finishing the deployment

Run these from a machine that can reach fly.io.

### 5.1 Required secrets

| Secret | Where it comes from |
|---|---|
| `SUPABASE_URL` | §3 table |
| `SUPABASE_ANON_KEY` | §3 table |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API. **Server-side only.** |
| `SUPABASE_JWT_SECRET` | Supabase → Project Settings → **JWT Keys** → JWT Secret. The API verifies every inbound token's HS256 signature with it. |
| `DATABASE_URL` | Supabase → Connect → **pooled** connection string, **port 6543**. The direct port exhausts connections under `--workers 2` plus a worker process. |
| `OPENAI_API_KEY` | Your provider. Required at startup. |
| `CORS_ALLOWED_ORIGINS` | `https://nexus-final-thesis.vercel.app` for the cross-origin shape |

### 5.2 Commands

```bash
fly auth login                       # or export FLY_API_TOKEN=...
fly launch --no-deploy --name nexus-api --region sin   # only if the app does not exist yet

fly secrets set \
  SUPABASE_URL=https://tthaxwiootsbidryiboe.supabase.co \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_JWT_SECRET=... \
  DATABASE_URL='postgresql://postgres.tthaxwiootsbidryiboe:...@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres' \
  OPENAI_API_KEY=... \
  CORS_ALLOWED_ORIGINS=https://nexus-final-thesis.vercel.app

fly deploy                           # = make deploy-api; builds web + worker from the Dockerfile
```

Then confirm both process groups are up, since a silently crashed worker means
uploads stay `pending` forever:

```bash
fly status
fly logs --process worker
curl -fsS https://nexus-api.fly.dev/health
curl -fsS https://nexus-api.fly.dev/ready
```

`/ready` is the meaningful one: it fails if the database is unreachable, whereas
`/health` deliberately only proves the process is alive.

### 5.3 Supabase auth settings — DONE, and a warning

**Already applied** to `tthaxwiootsbidryiboe`:

- **Site URL**: `https://nexus-final-thesis.vercel.app`
- **Redirect allow-list**: that origin, `/**` under it, the `nexus-git-main-…`
  branch alias, and `http://localhost:5173` (+ `/**`) so local sign-in still works

The project shipped with Site URL `http://localhost:3000`, so every confirmation
and recovery mail pointed at a host that does not exist for the user. A signup
would have been uncompletable.

> **`update_auth_config` is a full replace, not a patch.** Fields you omit are
> reset to `null`, not left alone. Applying it dropped
> `rate_limit_email_sent` (2 → `null`), `sms_provider` (`twilio` → `null`), and
> the `sessions_inactivity_timeout` / `sessions_timebox` values (0 → `null`).
> None of those are used by this app — SMS is disabled, sessions are
> Supabase-managed, and `/auth/v1/recover` returns 429 independently of the
> setting — but **read the whole config before calling this tool**, and re-send
> every field you want to preserve.

### 5.4 Migration-history hygiene

The live project's `supabase_migrations.schema_migrations` contains the 19
migrations under the repository's own file names, plus one row named
`seed_local_development_corpus`.

The seed is deliberately **not** a repository migration (`supabase/seed.sql`),
but it was applied through the migrations tool so it would run inside a single
transaction — the seed depends on a `pg_temp` function that only lives for one
transaction. The row is accurate history, not drift.

A scratch row, `probe_pg_temp_scope` (a throwaway temp-function visibility check),
has been deleted so the history reflects only real schema work.

## 6. Before real users

1. **Delete the seed users.** `alice@nexus.local`, `bob@nexus.local`,
   `carol@nexus.local` all have the published password `password123`. Removing
   the `auth.users` rows cascades through `profiles`, `workspaces` and everything
   scoped to them.
2. **Decide on signup.** Signup is currently enabled with email confirmation
   per Supabase defaults; set `disable_signup` if this is a private deployment.
3. **Rotate the database password** once the API is connected through the pooler,
   which does not need it in the frontend at all.
4. **Close the remaining audit findings** tracked in #17 (per-process rate
   limiting), #19 (`add_member` 404-vs-409), #23 (dev-only advisories), plus the
   absent backup/restore drill and worker heartbeat in #20. (#18, error
   tracking, is now wired — see §7.)
5. **Do not claim production readiness.** Per the audit's own rule, deployment
   checks (#20) must pass first, and the API is not deployed.

## 7. Fixes applied while reviewing the deployable image

Three defects were found by reading the code against the *deployment* shape
rather than the local one. Each has a regression test in
`apps/api/tests/unit/test_deployment_failures.py`, and each test was verified to
**fail** with the fix reverted — a test that cannot fail is decoration.

### 7.1 The pooler would have broken every query (fixed)

`DEPLOYMENT.md` requires the **pooled** `DATABASE_URL` (:6543, Supavisor) because
the direct port runs out of connections under `--workers 2` plus a worker. But
`asyncpg` caches server-side prepared statements **per connection**, and the
pooler multiplexes many clients onto a small set of backends. A statement
prepared on backend A can be executed on backend B, which is exactly the
condition that produces `DuplicatePreparedStatementError` /
`InvalidSQLStatementNameError`.

The pool is now created with `statement_cache_size=0`, which is what pgbouncer's
documentation prescribes for `pool_mode=transaction`. **This would not have
shown up locally or in CI**, because a direct connection never remultiplexes.

### 7.2 The development CORS default survived into production (fixed)

`CORS_ALLOWED_ORIGINS` defaults to `http://localhost:5173`. In production that
rejects the browser's preflight — but only once the frontend calls the API,
because Supabase auth itself is not CORS-bound. So login would appear to work and
*every* subsequent request would fail, which is the most confusing possible
presentation. `ENVIRONMENT=production` with the default now raises at boot with a
message naming the variable.

### 7.3 `SENTRY_DSN` was read and ignored (fixed)

The setting existed; nothing consumed it. A deployment that set it believed it
had error tracking while unhandled exceptions went to stdout and vanished with
the machine. `app/core/error_tracking.py` now initialises the SDK when a DSN is
present, filters the `/health` and `/ready` probes (Fly calls them every 15–30 s;
reporting them would burn the quota on noise), and — deliberately — never lets
Sentry break startup: no DSN, missing SDK, or a failing `init` all degrade to a
logged warning.

## 8. What breaks next, in the order you will hit it

Predictions, not findings. Each is a real failure mode of this stack; the first
two are near-certainties on first deploy.

1. **Supabase auth `redirect_to` will silently fall back to the Site URL.** The
   allow-list uses globs (`/**,`), and Supabase matches them as *patterns*, so
   any route not covered — a note deep link, a `/settings` return path — is
   dropped rather than rejected loudly. The user lands on the dashboard instead
   of where they were. Cost: a confusing redirect, not data loss.

2. **Email confirmation will rate-limit at about two messages per hour.** The
   project is on the free plan using Supabase's built-in SMTP, which is
   explicitly for testing. The third person to sign up in an hour gets a 429 and
   no mail. Fix: custom SMTP (Resend/Postmark) before anyone outside the team
   signs up.

3. **`/ready` will flap the machine if the pooler briefly drops.** Fly restarts a
   machine whose probe fails. `/ready` opens a connection each time, so a pooler
   hiccup during a deploy turns into a restart loop. Consider a grace period or
   making `/ready` tolerate one failed attempt.

4. **Two `web` machines silently double the rate limit.** `RateLimiter` is a
   per-process dict; the code and `/api/v1/meta` both say `distributed: false`.
   `min_machines_running = 1`, so this is correct today — but scaling `web` for
   load makes the limit 2× with no warning. Tracked as #17.

5. **The worker will look healthy while doing nothing.** It has no HTTP surface
   (by design), so a crashed worker shows up only as documents stuck in
   `pending`. There is no heartbeat. This is the highest-value missing signal in
   the system: `fly status` will say the app is up while every upload silently
   never processes.

6. **A slow upload will fail at five minutes.** Uploads go browser → Storage
   directly, using a signed URL that expires after
   `SIGNED_UPLOAD_TTL_SECONDS=300`. A 25 MiB file on a weak mobile connection is
   comfortably slower than that, and the failure surfaces as an opaque storage
   error with no resume — the user can only start over. Raise the TTL to match
   the largest permitted file on the slowest connection you support.

   (The key layout itself is safe: the API builds
   `{workspace_id}/{document_id}/{filename}` server-side, so the bucket policy
   can rely on segment 1 being the workspace id and a client cannot choose it.)

7. **A 50 MiB upload will exceed the function/worker timeout.** The bucket allows
   50 MiB, but ingest fetches, extracts, chunks and embeds in one job. A large
   PDF with a slow provider can exceed `job_stale_after_seconds` (900 s) and be
   retried — processing it twice and doubling the provider bill.

8. **Reindexing is all-or-nothing and blocking.** A model change requires
   `POST /embeddings/reindex` for every workspace, which re-embeds every chunk
   with no progress reporting beyond the job row.

9. **Switching Supabase to asymmetric signing keys would break auth instantly.**
   `app/core/security.py` pins `_ALGORITHMS = ("HS256",)` and verifies with
   `SUPABASE_JWT_SECRET`. Supabase is moving projects to asymmetric
   (`RS256`/ES256) signing keys with a JWKS endpoint; the moment this project is
   migrated — which the dashboard offers, and which also breaks `legacy` JWT
   secrets — every inbound token fails verification and no one can log in.
   Before pressing that button, add JWKS support and make the algorithm list
   explicit rather than HS256-only. This is the single change most likely to
   cause a total outage while looking like a routine settings toggle.
