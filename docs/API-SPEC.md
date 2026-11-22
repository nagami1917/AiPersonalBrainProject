# NEXUS — API Specification

Status: **design (spec phase)** — no handlers implemented yet
Base URL: `https://api.nexus.app` (Fly.io) · all paths under `/v1`

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Auth | `Authorization: Bearer <supabase_access_token>` on every route except `/health` and `/v1/meta` |
| User identity | Derived **only** from the verified JWT (`sub` claim). Any `user_id` in a request body is ignored, not trusted |
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
| 403 | `forbidden` | Authenticated but not the owner |
| 404 | `not_found` | Absent **or** not owned (never leak existence) |
| 409 | `duplicate_document` | Checksum already ingested |
| 413 | `file_too_large` | Over 50 MB |
| 422 | `quota_exceeded`, `unprocessable_document` | Valid request, cannot be fulfilled |
| 429 | `rate_limited` | Includes `Retry-After` |
| 502/503 | `provider_unavailable` | LLM/embedding provider down |

`404` vs `403` policy: a resource the caller does not own always returns `404`. Ownership is not disclosed.

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

### 3.2 Documents

#### `POST /v1/documents/upload-url`
Request a signed upload target. The API validates type/size and reserves the path; bytes never transit FastAPI.

```json
// request
{"filename": "thesis.pdf", "mime_type": "application/pdf", "file_size": 1843200}

// 200
{
  "document_id": "uuid",          // pre-allocated; client echoes it on confirm
  "storage_path": "user_…/doc_…/thesis.pdf",
  "upload_url": "https://…",       // signed, 5-minute TTL, PUT
  "expires_at": "2026-11-22T12:35:00Z"
}
```
Rejections: `unsupported_file_type`, `file_too_large`, `quota_exceeded`.

#### `POST /v1/documents`
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

#### `GET /v1/documents`
Query params: `status`, `collection_id`, `tag`, `q` (title/keyword), `limit`, `cursor`, `sort` (`updated_at|-created_at|title`).
Returns summaries (no `raw_text`).

#### `GET /v1/documents/{id}`
Full document incl. `raw_text` for notes, `summary`, `processing_stage`, tag list, chunk count. `404` if not owned.

#### `PATCH /v1/documents/{id}`
Updatable: `title`, `collection_id`, `tags` (full replace). Editing `raw_text` (notes only) increments `version` and enqueues `reindex`.

#### `DELETE /v1/documents/{id}`
Soft delete (`deleted_at = now()`), enqueues `purge`. Returns `204`. Chunks are removed immediately so the document stops appearing in retrieval, before the storage object is cleaned up.

#### `POST /v1/documents/{id}/reprocess`
Force re-extraction + re-chunking + re-embedding. Accepts `{"reason": "…"}`. Used after a chunking-settings change or a previously failed ingest.

#### `GET /v1/documents/{id}/chunks`
Paginated chunk preview with offsets and `heading_path` — the debugging and citation-preview surface.

#### `GET /v1/documents/{id}/status`
Lightweight polling target for the ingest progress UI: `{status, processing_stage, progress_pct, error_message}`. SSE variant `/v1/documents/{id}/events` streams stage transitions.

---

### 3.3 Notes

Notes are documents with `source_type='note'`. Kept as distinct endpoints because the payloads differ (no storage, immediate text).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/notes` | Create from `{title, body, collection_id, tags}` → enqueues chunk/embed job, returns `202` with `status: processing` |
| `PATCH` | `/v1/notes/{id}` | Edit body → version bump + reindex |
| `GET` | `/v1/notes` | Same filters as documents, forced `source_type='note'` |

#### `POST /v1/notes/{id}/save-conversation`
Turns a chat answer into a note, preserving `message_citations` as note metadata. This is how a good answer becomes durable knowledge.

---

### 3.4 Collections & tags

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/collections` | Flat list with `parent_id`; the client builds the tree |
| `POST` | `/v1/collections` | `{name, parent_id?, description?, color?}` — depth ≤ 3, sibling names unique |
| `PATCH` | `/v1/collections/{id}` | Rename, move (`parent_id` cycle-checked server-side) |
| `DELETE` | `/v1/collections/{id}` | Deletes folder; child documents → `collection_id = NULL` |
| `GET` | `/v1/tags` | With counts |
| `POST` | `/v1/tags/merge` | `{source_tag_id, target_tag_id}` — dedupe cleanup |

---

### 3.5 Search

#### `POST /v1/search`
Retrieval only — no LLM. Used by the search page and as the debug surface for RAG quality.

```json
// request
{
  "query": "jwt secret fly",
  "mode": "hybrid",                 // hybrid | semantic | keyword
  "filters": {"collection_id": "uuid", "tag": "devops",
              "created_after": "…", "created_before": "…",
              "document_ids": ["uuid"]},
  "limit": 20,
  "include_chunks": true
}
// 200
{
  "results": [
    {"document_id": "…", "document_title": "Deployment Notes",
     "chunk_id": "…", "snippet": "…", "highlight_ranges": [[12,28]],
     "page": 4, "score": 0.83, "matched_arm": "both"}
  ],
  "total_candidates": {"vector": 40, "keyword": 22}
}
```
`matched_arm` exposes *why* a result surfaced — invaluable when a search disappoints.

#### `GET /v1/search/suggest?q=…`
Entity/title prefix suggestions. Backed by `pg_trgm`; the only place typo tolerance is offered.

---

### 3.6 Chat

#### `POST /v1/chat`
Non-streaming answer. Same body as the streaming variant, minus `stream`.

#### `POST /v1/chat/stream`
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
| `GET` | `/v1/conversations` | Recent conversations, paginated |
| `POST` | `/v1/conversations` | Empty conversation, auto-titled later |
| `GET` | `/v1/conversations/{id}` | Full message list with citations |
| `PATCH` | `/v1/conversations/{id}` | Rename / archive |
| `DELETE` | `/v1/conversations/{id}` | Cascade messages + citations |
| `POST` | `/v1/messages/{id}/feedback` | `{helpful: bool, reason?}` — the cheapest source of eval material |

---

### 3.7 Graph

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/graph/entities?q=&type=&limit=` | Entity list with mention counts |
| `GET` | `/v1/graph/entities/{id}` | Entity detail: description, sources, top neighbours |
| `GET` | `/v1/graph/entities/{id}/neighbors?depth=1..2` | Graph slice for the visualisation |
| `GET` | `/v1/graph/subgraph?document_id=` | Entities within one document |
| `PATCH` | `/v1/graph/entities/{id}` | Rename / merge (`merge_into: uuid`) — user-corrected knowledge |

Graph is **read-mostly and derived**: it is a view over chunks, not a second source of truth. Merging entities rewrites `chunk_entities` and `entity_edges` rows.

---

### 3.8 Jobs & usage (own data only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/jobs?status=&document_id=` | The user's own processing queue, for an honest activity view |
| `POST` | `/v1/jobs/{id}/retry` | Requeue a `failed`/`dead` job |
| `GET` | `/v1/usage` | Storage used, documents, tokens this month, cost estimate |

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

## 6. Contract tests to write alongside handlers

1. A token for user A cannot read, mutate, or search any object owned by user B — asserted for **every** route with an object id.
2. `DELETE /documents/{id}` makes the document immediately invisible to `/search` and `/chat` retrieval.
3. `POST /chat` with an unanswerable question returns `grounded:false`, `citations: []`, and **no** LLM call (asserted at the provider stub).
4. A fabricated `[9]` marker in a model response is dropped and produces zero citations for that marker.
5. Replaying the same upload returns `409` and does not create a second `documents` row.
6. Every non-2xx response conforms to the error envelope.
