# NEXUS — API Specification

Status: **implemented in the scaffold** — routes below exist under `apps/api/app/api/routes`
Base URL: `https://api.nexus.app` (Fly.io) · all paths under `/v1`

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Auth | `Authorization: Bearer <supabase_access_token>` on every route except `/health` and `/v1/meta` |
| User identity | Derived **only** from the verified JWT (`sub` claim). Any `user_id` in a request body is ignored, not trusted |
| Workspace scope | Every tenant route is `/v1/workspaces/{workspace_id}/…`. Membership is resolved server-side; **not a member → `404`**, never `403` |
| Content type | `application/json` unless noted (`multipart` is avoided — uploads go direct to Storage) |
| IDs | UUID strings |
| Timestamps | RFC 3339 UTC (`2026-11-22T12:30:00Z`) |
| Pagination | Cursor-based: `?limit=25&cursor=<opaque>` → `{items, next_cursor}`. No offset pagination: it drifts under concurrent writes |
| Versioning | Breaking changes go to `/v2`; additive fields never break clients |
| Request ID | `X-Request-Id` echoed on every response; appears in logs and error payloads |

### Error envelope

```json
{
  "error": {
    "code": "document_not_ready",
    "message": "This document is still being processed.",
    "details": {"status": "processing", "stage": "embedding"},
    "request_id": "req_01H…"
  }
}
```

| HTTP | `code` examples | Meaning |
|---|---|---|
| 400 | `invalid_request`, `unsupported_file_type` | Malformed or unsupported input |
| 401 | `unauthorized`, `token_expired` | Missing/invalid JWT |
| 403 | `insufficient_role` | A member whose role is too low (e.g. a viewer attempting a write) |
| 404 | `not_found` | Absent **or** not owned (never leak existence) |
| 409 | `duplicate_document` | Checksum already ingested |
| 413 | `file_too_large` | Over 50 MB |
| 422 | `quota_exceeded`, `unprocessable_document` | Valid request, cannot be fulfilled |
| 429 | `rate_limited` | Includes `Retry-After` |
| 502/503 | `provider_unavailable` | LLM/embedding provider down |

`404` vs `403` policy: a workspace the caller is not a member of, and any resource inside it, returns `404` — membership is not disclosed. `403` is reserved for a caller who *is* in the workspace but whose role forbids the action, because that distinction is useful to them and leaks nothing.

---

## 2. Authentication

Supabase Auth owns signup, login, OAuth, password reset, and token refresh. **FastAPI implements no auth UI and no password handling.**

- Frontend obtains a session from Supabase; the access token is attached to every API call.
- FastAPI verifies the JWT signature (JWKS, cached; key rotation handled), validates `aud`/`iss`/`exp`, then sets the request's user context.
- A `POST /v1/auth/session-check` endpoint exists only for the client to validate a token after a cold start and receive the user's profile + quota state in one call.

---

## 3. Endpoints

### 3.1 Meta

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness. No auth. Returns `{status, version, commit}` |
| `GET` | `/v1/meta` | Public capability document: supported file types, size limits, embedding model id, feature flags. The frontend uses this to avoid hardcoding limits |

---

### 3.2 Workspaces & members

The tenant root. A user always has at least one — the personal workspace created at signup.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/workspaces` | Workspaces the caller belongs to, with their role and member count |
| `POST` | `/v1/workspaces` | `{name}` → creates a non-personal workspace; caller becomes owner (`201`) |
| `GET` | `/v1/workspaces/{id}` | One workspace the caller belongs to |
| `PATCH` | `/v1/workspaces/{id}` | Rename — `admin` or above |
| `DELETE` | `/v1/workspaces/{id}` | Owner only; `422 personal_workspace` for the personal one |
| `GET` | `…/members` | Member roster with roles |
| `POST` | `…/members` | `{email, role}` — adds an **existing** account; `404 user_not_found` otherwise |
| `PATCH` | `…/members/{user_id}` | Change role; the owner's role cannot be changed |
| `DELETE` | `…/members/{user_id}` | Remove a member, or leave; the owner cannot be removed |

Roles are `owner | admin | member | viewer`. `owner` is unique per workspace (partial unique
index). Email invitations are deliberately not implemented — the endpoint adds a known account by
address rather than promising a delivery flow that does not exist.

---

### 3.3 Documents

All document routes are scoped: `/v1/workspaces/{workspace_id}/documents…`.

#### `POST …/documents/upload-url`
Request a signed upload target. The API validates type/size and reserves the path; bytes never transit FastAPI.

```json
// request
{"filename": "thesis.pdf", "mime_type": "application/pdf", "file_size": 1843200}

// 200
{
  "document_id": "uuid",          // pre-allocated; client echoes it on confirm
  "storage_path": "{workspace_id}/doc_…/thesis.pdf",
  "upload_url": "https://…",       // signed, 5-minute TTL, PUT
  "expires_at": "2026-11-22T12:35:00Z"
}
```
Rejections: `unsupported_file_type`, `file_too_large`, `quota_exceeded`.

#### `POST …/documents`
Confirm an upload and enqueue ingestion.

```json
// request
{
  "document_id": "uuid",           // must match the id from upload-url
  "title": "Master's Thesis",
  "collection_id": "uuid|null",
  "tags": ["research", "ml"]
}
// 201
{"document": { …document object… }, "job_id": "uuid"}
```
Returns `409 duplicate_document` with the existing document in `details` if the checksum matches a live document.

#### `GET …/documents`
Query params: `status`, `collection_id`, `limit`, `cursor`.
Returns summaries (no `raw_text`).

#### `GET …/documents/{id}`
Full document incl. `raw_text`, `summary`, `processing_stage`, tag list, chunk count. `404` if not in this workspace.

#### `PATCH …/documents/{id}`
Updatable: `title`, `collection_id`, `tags` (full replace). Requires `member` or above.

#### `DELETE …/documents/{id}`
Soft delete (`deleted_at = now()`), enqueues `purge`. Returns `204`. Chunks are removed immediately so the document stops appearing in retrieval, before the storage object is cleaned up.

#### `POST …/documents/{id}/reprocess`
Force re-extraction + re-chunking + re-embedding. Accepts `{"reason": "…"}`. Used after a chunking-settings change or a previously failed ingest.

#### `GET …/documents/{id}/chunks`
Paginated chunk preview with offsets and `heading_path` — the debugging and citation-preview surface.

#### `GET …/documents/{id}/status`
Lightweight polling target for the ingest progress UI: `{id, kind, status, processing_stage, progress_pct, error_message}`. Notes expose the same shape at `…/notes/{id}/status`, so one poller serves both.

---

### 3.4 Notes

Notes are a **separate table** from documents (`notes` vs `documents`), sharing the same ingestion,
chunking and citation machinery. They are separate because they are edited constantly whereas a
document is an immutable upload, and they need different columns, indexes and retention.

Notes are therefore semantically distinct but retrieval-compatible: a note chunk and a document
chunk live in one `document_chunks` table with exactly one parent, so both compete in the same
search ranking and both can be cited.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `…/notes` | Create from `{title, body, collection_id, tags}` → enqueues `ingest_note`, returns `202` with `{note, job_id}` |
| `GET` | `…/notes` | List; filters `status`, `collection_id`, `limit`, `cursor` |
| `GET` | `…/notes/{id}` | Full note incl. `body`, `summary`, tags, `chunk_count` |
| `PATCH` | `…/notes/{id}` | Edit `title`/`body`/`collection_id`/`tags`. A body edit bumps `version` and enqueues a re-index |
| `DELETE` | `…/notes/{id}` | Soft delete + enqueue `purge`; chunks removed immediately |
| `GET` | `…/notes/{id}/status` | Same shape as document status |
| `POST` | `…/notes/{id}/reprocess` | Force re-chunk + re-embed |

`save-conversation` (turning an answer into a note) is **not implemented**: it is a genuinely
useful capture path, but it needs the citation snapshot carried into the new note's metadata and is
deferred rather than half-built.

---

### 3.5 Collections & tags

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/collections` | Flat list with `parent_id` + `note_count` / `document_count`; the client builds the tree |
| `POST` | `…/collections` | `{name, parent_id?, description?, color?}` — depth ≤ 3, sibling names unique per workspace |
| `PATCH` | `…/collections/{id}` | Rename or move (`parent_id` self-check) |
| `DELETE` | `…/collections/{id}` | Deletes the folder; notes and documents → `collection_id = NULL`. Never deletes content |
| `GET` | `…/tags` | With `note_count` / `document_count` / `usage_count` |

Tag merging is not implemented; tags are renamed implicitly by re-tagging (`PATCH` with a `tags`
list replaces the set).

---

### 3.6 Search

#### `POST …/search`
Retrieval only — no LLM. Used by the search page and as the debug surface for RAG quality.

```json
// request
{
  "query": "jwt secret fly",
  "mode": "hybrid",                 // hybrid | semantic | keyword
  "filters": {"collection_id": "uuid", "kind": "note",
              "created_after": "…", "created_before": "…",
              "source_ids": ["uuid"]},
  "limit": 20,
  "include_chunks": true
}
// 200
{
  "results": [
    {"source_kind": "document", "source_id": "…", "source_title": "Deployment Notes",
     "chunk_id": "…", "snippet": "…",
     "page": 4, "score": 0.83, "matched_arm": "both"}
  ],
  "total_candidates": {"vector": 40, "keyword": 22}
}
```
`matched_arm` exposes *why* a result surfaced — invaluable when a search disappoints.

#### `GET …/search/suggest?q=…`
Entity/title prefix suggestions. Backed by `pg_trgm`. **Not implemented yet** — the trigram
indexes exist (migration `0010`) but no route consumes them.

---

### 3.7 Chat

#### `POST …/chat`
Non-streaming answer. Same body as the streaming variant, minus `stream`.

#### `POST …/chat/stream`
SSE. See [RAG-PIPELINE.md §4.4](./RAG-PIPELINE.md#44-streaming) for the event contract.

```json
// request
{
  "conversation_id": "uuid|null",   // null starts a new conversation
  "message": "Where did I set the JWT secret?",
  "filters": {"collection_id": "uuid"},
  "options": {"max_sources": 8, "answer_style": "concise"}
}
```
Server-controlled, never client-controlled: model id, temperature, token budget, retriever k, reranking on/off.

Response includes `grounded`, `citations[]`, `retrieval_debug` (only when `?debug=1` and the caller owns the conversation).

#### Conversations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/conversations` | Recent conversations with message counts |
| `GET` | `…/conversations/{id}` | Full message list with citations (resolved from `message_sources`) |
| `PATCH` | `…/conversations/{id}` | Rename |
| `DELETE` | `…/conversations/{id}` | Cascade messages + citations |
| `POST` | `…/messages/{id}/feedback` | `{helpful: bool, reason?}` — the cheapest source of eval material |

Conversations are created implicitly by the first chat message; there is no empty-conversation
endpoint. Each citation in the transcript carries `source_kind`, `source_title`, `quote`, `page`,
`heading_path` and `score`.

---

### 3.8 Graph

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/graph/entities?q=&type=&limit=` | Entity list with mention counts |
| `GET` | `…/graph/entities/{id}` | Entity detail: links (in/out), mentioning chunks with their source kind |
| `GET` | `…/graph/links?limit=` | Weighted `knowledge_links` edges for the visualisation |

Graph is **read-mostly and derived**: it is a view over `chunk_entities`, not a second source of
truth. Entity rename/merge is not implemented, so nothing can desynchronise the graph from the
chunks it was extracted from.

---

### 3.9 Jobs (read-only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/jobs?status=&kind=&source_id=` | The workspace's processing queue, for an honest activity view |

The queue is **read-only to users**: enqueueing is a server-side decision (`enqueue_job()` checks
`can_write_workspace()` internally). Manual retry and the usage/cost summary are not implemented;
retries happen automatically with exponential backoff, and `usage_events` is recorded but not yet
exposed.

---

## 4. Rate limits

Per user, token-bucket, enforced in FastAPI (Redis optional later):

| Bucket | Limit |
|---|---|
| Chat / search | 20 req/min, 300 req/hour |
| Uploads | 30 req/min |
| Everything else | 120 req/min |

`429` carries `Retry-After` and a `X-RateLimit-*` header set. Auth endpoints are Supabase's responsibility.

---

## 5. Streaming & long-running work

- Nothing slow happens inside a request. Upload confirmation returns `201` with a `job_id` immediately; extraction and embedding happen in the worker.
- Only chat streams (SSE). Everything else is poll-or-fetch.
- Clients must tolerate `202 Accepted` with `status: processing` on any content-creating call.

---

## 6. Contract tests

Implemented in `apps/api/tests/`:

1. A caller cannot read, mutate, or search anything in a workspace they are not a member of — a
   foreign workspace id is a `404` at the route boundary **and** zero rows at the SQL boundary
   (`tests/db/test_chat_grounding_e2e.py`, `tests/db/test_api_workspaces.py`).
2. A `viewer` can read but cannot write: `403 insufficient_role` from the API, and the RLS write
   policy refuses the statement even if the API is bypassed (`supabase/tests/tenancy.sql`).
3. `DELETE …/documents/{id}` removes chunks immediately, so the document vanishes from
   `/search` and from chat retrieval before the storage object is purged.
4. An unanswerable question returns `grounded: false` **without calling the LLM**.
5. A fabricated citation marker persists no `message_sources` row.
3. `POST /chat` with an unanswerable question returns `grounded:false`, `citations: []`, and **no** LLM call (asserted at the provider stub).
4. A fabricated `[9]` marker in a model response is dropped and produces zero citations for that marker.
5. Replaying the same upload returns `409` and does not create a second `documents` row.
6. Every non-2xx response conforms to the error envelope.
