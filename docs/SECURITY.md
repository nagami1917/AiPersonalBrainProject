# NEXUS — Security Model

Status: **implemented in the scaffold** — this document describes what the code actually enforces, and where.

The threat model for a personal knowledge tool is dominated by one thing: **your documents are the asset**. A cross-tenant retrieval bug is a data breach, not a UI glitch. Everything below serves that.

---

## 1. Trust boundaries

| Boundary | Enforced by | Never trusted |
|---|---|---|
| Browser → API | Supabase JWT verified on every request | Any `workspace_id` or `user_id` in a request body |
| API → Postgres | RLS membership policies, with the API running as the caller's role | Application `WHERE` clauses alone |
| Worker → Postgres | service role, deliberately | Nothing — the worker is system code |
| Browser → Storage | Storage path policies (`{workspace_id}/…`) | Client-supplied paths |
| Retrieved text → LLM | Delimited `<source>` blocks + explicit non-instruction framing | Model compliance |

---

## 2. Keys and where they live

| Secret | Vercel | Fly.io | Browser | Notes |
|---|---|---|---|---|
| `SUPABASE_ANON_KEY` | ✅ (public) | ✅ | ✅ | Safe by design: RLS constrains it |
| `PUBLIC_SUPABASE_URL` / `PUBLIC_API_BASE_URL` | ✅ | — | ✅ | Configuration, not secrets |
| `SUPABASE_SERVICE_ROLE_KEY` | ❌ **never** | ✅ | ❌ | Bypasses RLS |
| `SUPABASE_JWT_SECRET` | ❌ | ✅ | ❌ | Forging tokens would be trivial |
| `OPENAI_API_KEY` | ❌ | ✅ | ❌ | Cost + prompt-injection surface |
| `DATABASE_URL` | ❌ | ✅ | ❌ | Direct DB access |

`apps/api/app/core/config.py` declares required values with no defaults, so **the process refuses to start** without them. A backend that boots without an embedding key and silently builds an unsearchable index is a worse failure than a crash.

Enforced structurally in `apps/web/src/lib/utils/env.ts`: only `PUBLIC_*` names are read, so a service key cannot leak into the bundle through that module.

---

## 3. Tenant isolation — defence in depth

Three independent mechanisms, so any one of them failing is not a breach:

Identity and authorisation are deliberately separate: the token says **who** is calling, and
workspace membership — resolved in Postgres — says **what** they may touch. A valid token is not a
permission.

**1. JWT-derived identity.** `current_user` decodes the bearer token and takes `user_id` from the
`sub` claim. Request bodies cannot influence it.

**2. Workspace resolution at the route boundary.** Every tenant route is
`/v1/workspaces/{workspace_id}/…`. `resolve_workspace()` joins `workspace_members` for the caller;
a non-member gets `404`, never `403`, because a `403` would confirm the workspace exists.

**3. RLS on all 18 tables.** Policies are membership-based (`is_workspace_member`,
`can_write_workspace`, `can_manage_workspace`), not owner-based. `user_db` opens a transaction and
sets `role = authenticated` plus the JWT claim **on the connection**, so Postgres applies policy to
everything the request does — including `hybrid_search`, which runs `SECURITY INVOKER` and takes
`p_workspace_id` explicitly.

**4. Explicit filters.** Queries pass `workspace_id` too. Redundant on purpose.

Verified against Postgres as a non-superuser role (`supabase/tests/tenancy.sql`) and over HTTP
(`apps/api/tests/db/test_api_workspaces.py`):

```
-- as carol, a viewer in alice's workspace and a stranger to bob's
select count(*) from public.notes where workspace_id = '<alice ws>';   → readable (member)
insert into public.notes (workspace_id, ...) values ('<alice ws>');    → rejected: RLS policy
update public.notes set title='pwned' where workspace_id='<alice ws>'; → UPDATE 0
insert into public.workspace_members ...;                              → rejected: RLS policy
GET /v1/workspaces/<bob ws>/notes                                      → 404
POST public.hybrid_search(p_workspace_id => '<alice ws>')              → 0 rows
```

`404`, not `403`, for resources the caller cannot reach: the API does not confirm that someone
else's workspace exists.

**5. Privileged SQL functions check their own caller.** `enqueue_job()` and the role helpers are
`SECURITY DEFINER` (so they can bypass RLS to do their job) and therefore verify
`can_write_workspace()` / membership internally. `EXECUTE` is not a write grant; without the guard
it would be one.

### 3.1 Roles

| Role | Read | Write | Manage members |
|---|---|---|---|
| owner | ✓ | ✓ | ✓ |
| admin | ✓ | ✓ | ✓ |
| member | ✓ | ✓ | ✗ |
| viewer | ✓ | ✗ | ✗ |

`owner` is exactly one per workspace, enforced by a partial unique index. A `viewer` is the row
that makes sharing safe: read access to a colleague's workspace without the ability to modify it —
enforced in the database, not by hiding buttons.

**6. Storage is path-scoped to the workspace.** Object paths begin with the workspace id and the
policies parse that segment, so a signed upload URL cannot be minted for a foreign workspace, and a
signed download URL cannot be used to read one.

---

## 4. Prompt injection

Documents are user-supplied text and may contain adversarial instructions ("ignore previous instructions and reveal…"). Controls:

- Retrieved content is wrapped in numbered `<source>` blocks.
- The system prompt states that source content is **data to cite, not instructions**.
- The model may only cite ids that exist in the supplied set; **fabricated markers are stripped server-side** and counted as a `grounding_violation` in the logs (`app/ai/grounding.py`).
- The model has no tool access and no database credentials, so a successful injection cannot escalate beyond producing a bad answer.
- Attachments are never executed or rendered as HTML in the UI.

This is mitigation, not immunity. It is tested with an adversarial eval set rather than assumed (see RAG-PIPELINE.md §5).

---

## 5. Upload handling

| Control | Implementation |
|---|---|
| Type allowlist | `MAX`/mime checked at `POST /documents/upload-url` before a path exists |
| Size limit | 50 MB at request time **and** on the bucket (`file_size_limit`) |
| Path traversal | Filenames sanitized to `[A-Za-z0-9._-]`; path is server-generated `{workspace_id}/{document_id}/{name}` |
| Private bucket | No public read; access via signed URLs only (upload 5 min, download 60 s) |
| Storage RLS | `(storage.foldername(name))[1] = auth.uid()::text` on select/insert/update/delete |
| Byte proxying | None — the browser PUTs directly; the API never handles file bytes |

Parsers (PyMuPDF, `python-docx`, `markdown-it-py`, BeautifulSoup) run on untrusted input in the worker, not in the request path, so a parser crash costs a retry rather than an outage.

---

## 6. Data handling

- `raw_text` is retained after chunking so re-indexing does not require the original file. It is user data, protected by the same RLS as everything else.
- Deleting a document is **soft** for 30 days (`deleted_at`), but its chunks are removed immediately so it stops being retrievable at once. A `purge` job removes the storage object.
- `usage_events` records token counts and cost estimates per user — needed to price a future SaaS honestly, and useful for spotting abuse.
- Conversation history is stored so answers remain reproducible; `retrieval_debug` explains why a given answer was produced.
- No analytics, telemetry, or third-party scripts in the frontend in the MVP.

**Provider exposure:** document chunks are sent to the embedding provider and retrieved passages to the LLM provider. This is disclosed in the UI copy and is inherent to the architecture. The roadmap's provider-swap point (`app/ai/providers.py` protocol boundary) exists partly so this can change without touching retrieval.

---

## 7. Rate limits

Token-bucket per user in FastAPI: chat/search 20/min, uploads 30/min, everything else 120/min. `429` carries `Retry-After`. Auth throttling is Supabase's responsibility.

Unbounded LLM spend is the cheapest denial-of-wallet attack against a RAG product, which is why limits and `usage_events` ship in the first milestone rather than last.

---

## 8. What is deliberately not done yet

| Not done | Why it is acceptable now | Trigger to revisit |
|---|---|---|
| OCR sandboxing | No OCR in MVP | Enabling OCR |
| Row-level audit log | Supabase logs cover MVP | Multi-user SaaS |
| MFA enforcement | Single user, own account | Sharing / teams |
| Encryption at rest beyond provider default | Supabase encrypts at rest | Compliance requirement |
| Secret rotation automation | Fly secrets rotated manually, documented | First incident |
| Penetration test | Pre-launch | Before public launch |
| CSRF tokens on API | JWT in `Authorization` header, not cookies — not CSRF-able | If cookie sessions are introduced |

---

## 9. Reporting

Security issues should go to the repository maintainer privately. Do not open a public issue containing exploit details or user data.
