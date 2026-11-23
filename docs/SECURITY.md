# NEXUS — Security Model

Status: **describes what the code enforces, and where.** Where a control is partial, it says so — the audit for this document found three rules that were documented but not implemented (rate limits, content inspection, audit trail), and each is now backed by code and a test.

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

The token must carry four things, and all four are checked: a valid `HS256` signature against
`SUPABASE_JWT_SECRET`, an unexpired `exp`, a non-empty `sub`, and an `aud` of `authenticated`.
Anything else is a `401` with a generic message — the response never says which check failed.

> **The `aud` check is manual on purpose.** `python-jose` with
> `audience=…, options={"verify_aud": True}` *skips* the audience comparison when the claim is
> absent, so a token minted without any `aud` claim was accepted. The claim is therefore asserted
> explicitly (`claims.get("aud")`). `tests/unit/test_jwt_verification.py` covers the missing-`aud`
> case; do not "simplify" it back to the library option.

**2. Workspace resolution at the route boundary.** Every tenant route is
`/api/v1/workspaces/{workspace_id}/…`. `resolve_workspace()` joins `workspace_members` for the caller;
a non-member gets `404`, never `403`, because a `403` would confirm the workspace exists.

**3. RLS on all 20 tables.** Policies are membership-based (`is_workspace_member`,
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
GET /api/v1/workspaces/<bob ws>/notes                                      → 404
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

**6. Storage is path-scoped to the workspace.** Object paths begin with the workspace id
(`nexus-documents/{workspace_id}/{document_id}/{filename}`) and the policies parse that segment, so a
signed upload URL cannot be minted for a foreign workspace and a signed download URL cannot read one.

The tenant segment is the **workspace**, not the user: a member who joins a workspace must be able to
download its files, so a per-user path would either break sharing or leak across tenants. Reads need
membership, writes need write permission — a viewer can download but not upload.

**7. The worker re-checks the path it is about to fetch.** The worker downloads with the service role,
which bypasses storage policies entirely. Before fetching, it verifies the stored path still begins
with its own workspace id, so a corrupted row cannot become a cross-tenant read. This is the one place
where the second line of defence is code rather than policy, and it is commented as such.

---

## 4. Prompt injection

Documents are user-supplied text and may contain adversarial instructions ("ignore previous instructions and reveal…").

**What is fully prevented — structural breakout.** Every passage is wrapped in `<source>…</source>`, and the system prompt says the content inside is data. That promise is only as good as the delimiter being trustworthy: a passage containing `</source>` closes the block early, and everything after it lands where the model expects *our* framing. `neutralize()` in `app/ai/rag/context_builder.py` rewrites every reserved delimiter inside untrusted text — including case variants and `<system>`/`<instructions>` — to an inert marker, and swallows a tag's attributes so a forged `<source id="7" …>` cannot leave a usable fragment behind. `tests/unit/test_prompt_injection.py` asserts that exactly one source block survives any payload, so a document cannot forge a citable source.

**What is mitigated, not prevented — persuasion.** A document that says "ignore your instructions" is a contest between our system prompt and its text; no check in this repository decides that contest. Layers:

- The system prompt states that source content is **data to cite, not instructions**, and that a source containing commands should be reported, not obeyed.
- The model may only cite ids that exist in the supplied set; fabricated markers are stripped server-side (`app/ai/rag/answer_generator.py`).
- An answer with no valid citation is returned as **ungrounded** rather than as fact.
- The model has no tool access and no database credentials, so a successful injection cannot escalate beyond producing a bad answer.
- These are exercised with adversarial payloads (`tests/unit/test_prompt_injection.py`) rather than assumed; see also RAG-PIPELINE.md §5.

Delimiters are neutralised; persuasion is defended in depth. The distinction is the honest one.

---

## 5. Upload handling

| Control | Implementation |
|---|---|
| **Extension allowlist** | `app/core/upload_validation.py`, at `POST /documents/upload-url` before a path exists |
| **Extension ↔ MIME agreement** | Both are client-supplied, so a disagreement (`.pdf` declared `text/plain`) is refused — one of the two claims is false |
| **Size limit** | 50 MB at request time **and** on the bucket (`file_size_limit`) |
| **Content inspection** | Magic bytes checked in the worker against the declared type, where the bytes first exist |
| Path traversal | Filenames sanitized to `[A-Za-z0-9._-]`; path is server-generated `{workspace_id}/{document_id}/{name}` |
| Private bucket | No public read; signed download URLs expire in 60 s |
| Storage RLS | First path segment must be a workspace the caller belongs to; writes need write permission |
| Download paths | Derived server-side from the document row, never from a request parameter |
| Byte proxying | None — the browser PUTs directly; the API never handles file bytes |

### 5.1 Why validation is split in two

The browser uploads straight to Storage, so **the API never sees the file at the moment it authorises the upload**. Both halves are needed:

- `validate_declared` (request time) — extension, declared MIME, declared size, and whether the first two agree. Catches honest mistakes and lazy spoofing.
- `validate_content` (worker) — magic bytes against the declared type. Catches a renamed `.exe`, a `.docx` that is not a ZIP container, and a "text" file containing binary.

`.txt` and `.md` have no magic number, so content sniffing cannot *confirm* them; instead the worker rejects binary content and the extractor decodes strictly. A text file that is not valid UTF-8 is not a text file, whatever it is named.

Parsers (PyMuPDF, `python-docx`, `markdown-it-py`, BeautifulSoup) run on untrusted input in the worker, not in the request path, so a parser crash costs a retry rather than an outage.

---

## 6. Data handling

- `raw_text` is retained after chunking so re-indexing does not require the original file. It is user data, protected by the same RLS as everything else.
- Deleting a document is **soft** for 30 days (`deleted_at`), but its chunks are removed immediately so it stops being retrievable at once. A `purge` job removes the storage object.
- `usage_events` records token counts and cost estimates per user — needed to price a future SaaS honestly, and useful for spotting abuse.
- Conversation history is stored so answers remain reproducible; `retrieval_debug` explains why a given answer was produced.
- No analytics, telemetry, or third-party scripts in the frontend in the MVP.

**Provider exposure:** document chunks are sent to the embedding provider and retrieved passages to the LLM provider. This is disclosed in the UI copy and is inherent to the architecture. The roadmap's provider-swap point (`app/ai/providers/base.py` protocol boundary) exists partly so this can change without touching retrieval.

---

## 7. Rate limits

`app/core/ratelimit.py`, applied through `app/core/guard.py`. Only the endpoints that call a provider are limited — chat, streaming chat, and summarisation. CRUD is not: a client autosaving a note every 1.5 s is normal behaviour, and throttling it would be an outage dressed as security.

**Two budgets, checked on every limited call:**

| Scope | Default | Why both |
|---|---|---|
| Per user | 30 / 60 s | One account hammering the provider — a retry loop in a tab left open |
| Per workspace | 240 / 60 s | The shared cost ceiling. A 20-member workspace should not get 20× one member's budget, and one leaked member account should not drain the tenant |

A per-user-only limit cannot protect the bill; a per-workspace-only limit lets one user lock everyone else out.

`429` carries `{"details": {"scope": "user" | "workspace", "retry_after": n}}`, so a client knows whether to back off or to tell the user. Both budgets are checked *before* either is incremented, so a workspace rejection does not also spend the human's personal allowance.

**Honest limits.** Counters are per process. With N API replicas the effective ceiling is N×, which `/api/v1/meta` states as `rate_limits.distributed: false` rather than implying precision that is not there. Postgres- or Redis-backed counting is the upgrade path; the interface is already async, so no route changes.

Unbounded LLM spend is the cheapest denial-of-wallet attack against a RAG product, which is why limits and `usage_events` ship in the first milestone rather than last.

---

## 8. Audit trail

Migration `0013_audit_events.sql`, written by `app/core/audit.py`, readable at
`GET /api/v1/workspaces/{id}/audit`.

**Recorded:** document upload / delete / reprocess / download, workspace delete, member add /
remove / leave / role change, note create / update / delete, collection create, and
`note.link.create` for a **newly** created wiki link.

Notes and links were the last gap here, and the more damaging half of it: a document's
bytes are immutable once uploaded, whereas a note's body is editable, so an unanswered
"who changed this?" matters more there than anywhere else. `note.link.create` records the
*delta* rather than the link set, because `replace_note_links` rewrites every outgoing link
on each save — recording the whole set would emit an identical event on every autosave and
train a reader to ignore the trail.

**Properties that make it evidence rather than logging:**

| Property | How |
|---|---|
| Append-only | No update or delete policy for any user role; asserted by a test |
| Actor cannot be forged | `record_audit` is `SECURITY DEFINER` and reads the actor from the JWT claim GUC, never a parameter |
| Cannot be planted across tenants | The function independently verifies the actor's membership |
| Survives its subject | `target_id` has no foreign key, so a deleted document's history remains |
| Outlives the account | `actor_id` is `ON DELETE SET NULL`; the row stays with the name gone, shown as such |
| Readable by admins, not members | `can_manage_workspace` in RLS plus `require_manage` in the route — a member gets 403, not an empty list |

**The leak this could have become.** The tempting implementation is `detail = row_to_json(document)`, which would write every document body into a permanently retained, admin-readable table. Instead `detail` is built from a **whitelist** (`SAFE_FIELDS`) that contains no free-text field at all: `content`, `raw_text`, `summary`, `answer` and friends are dropped at write time, and nested structures are rejected because a dict could smuggle anything past a key-name check. `tests/unit/test_audit_scrub.py` fails if anyone adds a body field to that set.

A failed audit insert **never fails the operation**: the delete already succeeded, and telling the user otherwise would be a lie. The failure is logged at ERROR so a broken trail is visible.

---

## 9. What is deliberately not done yet

| Not done | Why it is acceptable now | Trigger to revisit |
|---|---|---|
| OCR sandboxing | No OCR in MVP | Enabling OCR |
| Audit log retention/export | Append-only and queryable per workspace; no retention policy or export yet | Compliance requirement |
| MFA enforcement | Single user, own account | Sharing / teams |
| Encryption at rest beyond provider default | Supabase encrypts at rest | Compliance requirement |
| Secret rotation automation | Fly secrets rotated manually, documented | First incident |
| Penetration test | Pre-launch | Before public launch |
| CSRF tokens on API | JWT in `Authorization` header, not cookies — not CSRF-able | If cookie sessions are introduced |
| Distributed rate limiting | In-process counters; accurate for one replica, N× for N | More than one API instance |
| Audit retention policy | Table grows without pruning; no export | Compliance requirement |
| Extension sniffing for `.txt`/`.md` | No magic number exists; binary content and strict decode are checked instead | A signature format is adopted |
| Per-member spend quotas | Workspace ceiling only, not per-seat budgets | Billing per seat |

---

## 10. Reporting

Security issues should go to the repository maintainer privately. Do not open a public issue containing exploit details or user data.
