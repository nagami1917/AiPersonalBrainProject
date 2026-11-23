# NEXUS — API Specification

Status: **implemented in the scaffold** — routes below exist under `apps/api/app/api/routes`
Base URL: `https://api.nexus.app` (Fly.io) · all paths under `/v1`

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Auth | `Authorization: Bearer <supabase_access_token>` on every route except `/health` and `/api/v1/meta` |
| User identity | Derived **only** from the verified JWT (`sub` claim). Any `user_id` in a request body is ignored, not trusted |
| Workspace scope | Every tenant route is `/api/v1/workspaces/{workspace_id}/…`. Membership is resolved server-side; **not a member → `404`**, never `403` |
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
| 429 | `rate_limited` | `details.scope` and `details.retry_after` |
| 502/503 | `provider_unavailable` | LLM/embedding provider down |

`404` vs `403` policy: a workspace the caller is not a member of, and any resource inside it, returns `404` — membership is not disclosed. `403` is reserved for a caller who *is* in the workspace but whose role forbids the action, because that distinction is useful to them and leaks nothing.

---

## 2. Authentication

Supabase Auth owns signup, login, OAuth, password reset, and token refresh. **FastAPI implements no auth UI and no password handling.**

- Frontend obtains a session from Supabase; the access token is attached to every API call.
- FastAPI verifies the JWT signature (JWKS, cached; key rotation handled), validates `aud`/`iss`/`exp`, then sets the request's user context.
- `GET /api/v1/auth/me` is the cold-start call: it validates the token implicitly (it would not
  authorise without one) and returns the profile, the caller's workspaces, and
  `default_workspace_id` in one response. The frontend needs all three before it can render
  anything, and making it infer a default from the workspace list is how a user lands in the
  wrong workspace on first load.

```json
{
  "user": {"id": "uuid", "email": "a@b.c", "display_name": "Ada", "avatar_url": null},
  "workspaces": [{"id": "uuid", "name": "Personal", "slug": "personal", "role": "owner", "personal": true}],
  "default_workspace_id": "uuid"
}
```

---

## 2a. Two path shapes, one implementation

The spec lists resources flat (`GET /notes`, `POST /ai/chat`, `GET /graph`). This API nests
them (`GET /api/v1/workspaces/{id}/notes`) because a workspace in the path is how authorisation,
the RLS identity, and every other resource route work — and because a workspace that silently
falls back to "some workspace" is a tenancy hazard.

Both shapes are served, from one implementation each:

| Shape | Path | Workspace comes from |
|---|---|---|
| Scoped (canonical) | `/api/v1/workspaces/{id}/notes` | the path, always explicit |
| Flat (spec) | `/api/v1/notes?workspace_id=…` | the query parameter, or `profiles.default_workspace_id` |

The flat forms delegate to the scoped view functions, so a tenancy or `status` fix cannot be
made in one copy and forgotten in the other. An explicit `workspace_id` is authorised exactly
like the path form: **a workspace you are not a member of is `404`, never `403`**, because the
API does not confirm that someone else's workspace exists. Omitting it is not a bypass — it
selects your own default.

Aliases exist only where the meaning is unambiguous. `POST /documents/upload` has no alias: it
is a two-step signed-URL flow whose response depends on the workspace, and guessing a default
for a write that creates storage objects is not a convenience worth having.

---

## 3. Endpoints

### 3.1 Meta

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness. No auth. Returns `{status, version}`. Does not touch the database, so a database outage cannot restart a healthy process |
| `GET` | `/ready` | Readiness. No auth. Returns `{status, version, database}` — `200` when a database round-trip succeeds, `503` with `database: "unreachable"` when it does not. This is the probe `fly.toml` routes traffic on |
| `GET` | `/api/v1/meta` | Public capability document: supported file types, size limits, embedding model id, feature flags. The frontend uses this to avoid hardcoding limits |

---

### 3.1a Flat aliases (spec §5.1 shape)

Every entry delegates to its scoped counterpart. `workspace_id` is always optional and always
authorised the same way.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/auth/me` | Identity + workspaces + default workspace |
| `GET` | `/api/v1/notes` | List notes |
| `POST` | `/api/v1/notes` | Create note (`202`; chunking is asynchronous) |
| `GET` | `/api/v1/documents` | List documents |
| `GET` | `/api/v1/documents/{id}` | Document details |
| `DELETE` | `/api/v1/documents/{id}` | Delete document |
| `POST` | `/api/v1/documents/{id}/reprocess` | Reprocess (`202`) |
| `GET` | `/api/v1/collections` | List collections |
| `POST` | `/api/v1/collections` | Create collection |
| `GET` | `/api/v1/conversations` | Chat history |
| `GET` | `/api/v1/conversations/{id}` | Conversation with messages and citations |
| `GET` | `/api/v1/search?q=` | Search (`mode`, `limit`, `kind`, `collection_id`) |
| `GET` | `/api/v1/graph` | Nodes and edges in one snapshot |

`GET /api/v1/graph` returns `{nodes, edges, workspace_id}` from a single pair of queries, so a graph
view cannot render an edge whose node is missing because a second request had not arrived yet.

---

### 3.2 Workspaces & members

The tenant root. A user always has at least one — the personal workspace created at signup.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/workspaces` | Workspaces the caller belongs to, with their role and member count |
| `POST` | `/api/v1/workspaces` | `{name}` → creates a non-personal workspace; caller becomes owner (`201`) |
| `GET` | `/api/v1/workspaces/{id}` | One workspace the caller belongs to |
| `PATCH` | `/api/v1/workspaces/{id}` | Rename — `admin` or above |
| `DELETE` | `/api/v1/workspaces/{id}` | Owner only; `422 personal_workspace` for the personal one |
| `GET` | `…/members` | Member roster with roles |
| `POST` | `…/members` | `{email, role}` — adds an **existing** account; `404 user_not_found` otherwise |
| `PATCH` | `…/members/{user_id}` | Change role; the owner's role cannot be changed |
| `DELETE` | `…/members/{user_id}` | Remove a member, or leave; the owner cannot be removed |
| `GET` | `…/audit` | Sensitive-operation log — `admin` or above; see §4.2 |

Roles are `owner | admin | member | viewer`. `owner` is unique per workspace (partial unique
index). Email invitations are deliberately not implemented — the endpoint adds a known account by
address rather than promising a delivery flow that does not exist.

---

### 3.3 Documents

All document routes are scoped: `/api/v1/workspaces/{workspace_id}/documents…`.

#### `POST …/documents/upload-url`
Request a signed upload target. The API validates type/size and reserves the path; bytes never transit FastAPI.

```json
// request
{"filename": "thesis.pdf", "mime_type": "application/pdf", "file_size": 1843200}

// 200
{
  "document_id": "uuid",          // pre-allocated; client echoes it on confirm
  "file_path": "{workspace_id}/doc_…/thesis.pdf",
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
  "filename": "thesis.pdf",        // the user's name for the file
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

#### `GET …/documents/trash`
Deleted documents inside their retention window, each with `expires_at`. Mirrors `…/notes/trash`.

#### `DELETE …/documents/{id}`
Soft delete (`deleted_at = now()`), returns `204`. Chunks are removed immediately so the document stops appearing in retrieval at once. The **storage object is kept** until the purge runs, which is what makes restore possible; `purge` is scheduled `TRASH_RETENTION_DAYS` out (default 30) rather than immediately.

#### `POST …/documents/{id}/restore`
Undo a deletion. Returns the document as `pending` and re-queues ingestion from the original bytes — the file was never removed, but its chunks were.

#### `POST …/documents/{id}/reprocess`
Force re-extraction + re-chunking + re-embedding. Accepts `{"reason": "…"}`. Used after a chunking-settings change or a previously failed ingest.

#### `GET …/documents/{id}/chunks`
Paginated chunk preview with offsets and `heading_path` — the debugging and citation-preview surface.

#### `GET …/documents/{id}/status`
Lightweight polling target for the ingest progress UI: `{id, kind, status, processing_stage, progress_pct, error_message}`. Notes expose the same shape at `…/notes/{id}/status`, so one poller serves both.

#### `GET …/documents/{id}/download-url`
Signed URL for the original upload, expiring in 60 s. Path derived server-side; see §4.1.

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
| `POST` | `…/notes` | Create from `{title, content, content_format?, collection_id, tags}` → enqueues `ingest_note`, returns `202` with `{note, job_id}` |
| `GET` | `…/notes` | List; filters `status`, `collection_id`, `tag`, `q`, `limit`, `cursor` |
| `GET` | `…/notes/trash` | Deleted notes inside their retention window, each with `expires_at` |
| `GET` | `…/notes/{id}` | Full note incl. `content`, `content_format`, `summary`, tags, `chunk_count` |
| `PATCH` | `…/notes/{id}` | Edit `title`/`content`/`collection_id`/`tags`. A content edit bumps `version` and enqueues a re-index |
| `DELETE` | `…/notes/{id}` | Soft delete; chunks removed immediately, purge scheduled `TRASH_RETENTION_DAYS` out |
| `POST` | `…/notes/{id}/restore` | Undo a deletion; returns the note as `pending` and re-indexes it |
| `GET` | `…/notes/{id}/status` | Same shape as document status |
| `POST` | `…/notes/{id}/reprocess` | Force re-chunk + re-embed |

#### Note search: two different questions

`q` on the list endpoint is a **substring filter** over title and body, answered from Postgres in
one query. `POST /search` is **semantic** retrieval: it embeds the query, costs an API call, and
returns passages. They are not interchangeable, and the list endpoint deliberately does not use
retrieval for its search box — a keystroke should not bill an embedding.

`q` matches literally: it is implemented with `position(lower(x) in lower(q))`, not `ILIKE`, so a
query containing `%` or `_` does not act as a wildcard. Searching for `100%` finds the notes that
contain `100%`, not every note.

#### Delete and restore

Deletion is two-phase. The row is marked `deleted_at` and vanishes from every view immediately, but
it is only removed for good once `TRASH_RETENTION_DAYS` has elapsed (default 30). Until then it is
listed by `…/trash` with an `expires_at`, and `…/restore` brings it back.

Restoring returns the note as `pending` and re-queues ingestion. The chunks were deleted when the
note was trashed, so a restore that only cleared `deleted_at` would produce a note that is visible
but silently unsearchable.

A purge job that runs early is a **no-op**: eligibility is re-checked in SQL against `deleted_at`,
not trusted from the job's `run_after`, because retention is configured per environment and a job
may have been queued under a different value.

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

### 3.6a AI (`/api/v1/ai`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/ai/chat` | Ask NEXUS; workspace in the body |
| `POST` | `/api/v1/ai/stream` | Same, streamed as SSE |
| `POST` | `/api/v1/ai/summarize` | Summarize pasted text or one source |

`/ai/chat` accepts `question` as an alias for `message` (the spec's example uses `question`;
the canonical route and the frontend use `message`). Exactly one must be present.

`/ai/summarize` takes exactly one of `text` or `source_id`:

```json
{"workspace_id": "uuid", "text": "…", "style": "concise"}
{"workspace_id": "uuid", "source_id": "uuid", "style": "concise"}
```

It generates on demand rather than returning `documents.summary`. That column is written by an
enrichment job that may not have run yet, and is capped for list views — returning it here
would be either empty or silently stale. It also does **not** persist its output: the stored
summary is the job's, written under a version guard, and a second writer with different timing
would race it.

A `source_id` from another workspace is `404`, because it is resolved inside the authorised
workspace rather than looked up by id alone.

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

**Filters** (`filters` on search and chat): `collection_id`, `tag`, `created_after`, `created_before`,
`source_ids`, `kind`. They are applied once inside `hybrid_search`, so both arms see the same
candidate set. `tag` matches the parent's tag name case-insensitively; an unknown tag matches
**nothing** rather than falling back to "no filter", so a typo cannot silently return the whole
workspace.

#### Conversations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/conversations` | Recent conversations with message counts |
| `GET` | `…/conversations/{id}` | Full message list with citations (resolved from `message_sources`) |
| `PATCH` | `…/conversations/{id}` | Rename |
| `DELETE` | `…/conversations/{id}` | Cascade messages + citations |
| `POST` | `…/messages/{id}/feedback` | `{helpful: bool, reason?}` — the cheapest source of eval material |

Conversations are created implicitly by the first chat message; there is no empty-conversation
endpoint.

A conversation is **titled at creation** from its opening question, so the history list is
navigable without a second request (`GET …/conversations` returns it). A greeting or an empty
question produces `"New conversation"` rather than a `null` title, because an untitled row cannot
be found in a list. `PATCH` renames it; the title is never regenerated on a follow-up turn.

#### The citation shape

| Field | Meaning |
|---|---|
| `chunk_id` | The exact retrieved passage. The join key for everything else. |
| `source_kind` / `source_id` | Which table the id belongs to (`note` or `document`). |
| `source_title`, `filename`, `page`, `heading_path` | How a human recognises the source. `page` is `null` for formats without pages. |
| `quote` | The retrieved content itself, so a citation can be checked without another request. |
| `relevance` + `relevance_kind` | See below. |

`relevance` is the hit's fused RRF score rescaled so the best source in that answer is `1.0`.
It is a **relative ordering signal, not a confidence**, which is why the unit travels with it as
`relevance_kind: "relative"`. The raw RRF value is ~0.008–0.03, and publishing it directly made
every citation render as "1% match" — a fabricated measurement. The absolute judgement about
whether retrieval was good enough happens server-side before generation (`grounded: false` and no
citations at all), so a client should render `relevance` as a bar, never as a percentage.

`filename` is the uploaded file's name for document sources and the title for notes (which have no
file), denormalised into `message_sources` at write time so a rename or delete cannot rewrite
history. Replayed citations are rescaled per message, so a refresh does not change what the
numbers mean.

#### Streaming and grounding

`POST …/chat/stream` emits `sources` before generation, then `delta`s, then `citations`, then
`done`. One rule is load-bearing: **no text is emitted until the model has cited a real source.**
A model that invents `[9]`, or that answers without citing anything, produces no deltas at all —
the client receives only the refusal. The alternative (stream unverified text, then report
`grounded: false`) showed users the fabrication and then contradicted it in the same bubble, and
left an uncited answer entirely unretracted. The cost is that a grounded answer's first chunk is
released when its first valid citation arrives, not before.

---

### 3.6a Embedding model metadata and reprocessing

| Method | Path | Purpose |
|---|---|---|
| `GET` | `…/embeddings` | Chunk counts per embedding model vs. the configured one, plus how much work a reindex is |
| `GET` | `…/embeddings/health` | One-line state: `empty` / `current` / `mixed` / `stale` |
| `POST` | `…/embeddings/reindex` | Queue a `reindex` job per source whose vectors came from another model. Requires `{"confirm": true}` |

Every chunk records `embedding_model` and `embedding_dim`. Changing the model is a migration, not a
config tweak, because `document_chunks.embedding` is `vector(1536)` and the HNSW index is built for
one width — `core/schema_guard.py` refuses to boot on a width mismatch. What it cannot catch is a
swap at the *same* width, which leaves old vectors being ranked against new query vectors: cosine
distance across two embedding spaces is meaningless, and results get quietly worse rather than
failing. `state: "mixed"` is that condition, and it is why the API reports it and the worker logs
drift at startup.

`confirm` is required rather than defaulted to `false`, so a client that forgets it gets a `400`
instead of assuming a reindex started. Queueing is idempotent per source: a source already
pending is skipped, so the call can be retried safely.

---

### 3.7a Note links, stats and suggestions

#### `GET` `…/notes/{id}` — backlinks and outgoing links

The note detail response carries both directions of the note's wiki-link graph:

```json
{
  "backlinks": [{"id": "uuid", "title": "Q2 Plan", "status": "ready", "updated_at": "…"}],
  "outgoing_links": [
    {"target_title": "Q3 Budget", "target_id": "uuid", "resolved": true, "status": "ready"},
    {"target_title": "Not written yet", "target_id": null, "resolved": false, "status": null}
  ]
}
```

`[[wiki links]]` are extracted from the body on every write, resolved against note titles in
the same workspace, and stored in `note_links`. Matching is case- and whitespace-insensitive;
the *stored* title keeps the author's capitalisation, because that is what the graph displays.

Three properties are deliberate:

- **An unresolved link is kept, not dropped.** Writing `[[Q3 Budget]]` before that note exists
  is a promise, and the panel shows it as unwritten rather than pretending it was never made.
- **Creating a note re-resolves outstanding links to its title.** Otherwise a link written
  first would stay dangling forever.
- **A note is never its own backlink**, and links never cross workspaces — a `[[link]]` in one
  workspace cannot resolve to a note in another.

#### `GET` `…/stats`

One query, because the Welcome page's cards are read together and per-card requests would let
the numbers disagree on screen. Counts exclude soft-deleted rows.

```json
{"workspace_id": "uuid", "notes": 12, "notes_ready": 9, "documents": 4,
 "documents_ready": 3, "documents_processing": 1, "documents_failed": 0,
 "collections": 2, "tags": 7, "entities": 31, "links": 44, "chunks": 210,
 "conversations": 5, "note_links": 9}
```

#### `GET` `…/suggestions?note_id=&limit=`

Pairs of notes that are **related but not yet linked, in either direction**. Already-linked
pairs are excluded before ranking — a suggestion panel that proposes edges the graph already
has is noise the user learns to ignore.

Ranking is shared-tag count first, embedding proximity second: a shared tag is a claim the
user made deliberately, a vector is a guess. `reason` states the basis, and the UI shows it.

Pairs are ordered by title, not by id. (Constraining both orderings silently drops every pair
whose two orderings disagree — roughly half of them.)

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

Only endpoints that call a model provider are limited. CRUD is not — autosave every 1.5 s is normal
client behaviour, and throttling it would look like an outage.

| Scope | Default | Applies to |
|---|---|---|
| Per user | 30 / 60 s | `POST …/chat`, `…/chat/stream`, `POST /ai/chat`, `/ai/stream`, `/ai/summarize` |
| Per workspace | 240 / 60 s | the same set, counted across all members |

Both budgets are checked on every call, because they fail differently: a per-user limit alone cannot
protect the bill, and a per-workspace limit alone lets one member lock out everyone else. Chat and
summarise have *separate* pools, so alternating endpoints cannot multiply an allowance.

```json
{ "error": { "code": "rate_limited",
             "message": "You are making requests faster than this endpoint allows. Wait a moment and try again.",
             "details": { "scope": "user", "retry_after": 15 },
             "request_id": "req_…" } }
```

`details.scope` distinguishes your own traffic from a busy workspace. Limits are per process, and
`GET /api/v1/meta` reports them under `rate_limits` with `distributed: false` rather than implying a
precision the implementation does not have. Auth endpoints are Supabase's responsibility.

---

## 4.1 Document download

```
GET /api/v1/workspaces/{workspace_id}/documents/{document_id}/download-url
→ 200 { url, expires_at, filename, mime_type }
```

Returns a **short-lived** signed URL (60 s by default). The storage path is derived from the document
row, never accepted from the request — so there is no parameter to point at another workspace's
object. The document's own `file_path` is *not* returned by `GET /documents/{id}`: no screen needs it,
and it is exactly the value a path-manipulation attempt wants.

---

## 4.2 Audit trail

```
GET /api/v1/workspaces/{workspace_id}/audit?limit=50&before=<iso>&action=<prefix>&target_id=<uuid>
→ { items: [{ id, action, target_type, target_id, target_label, detail,
              actor_id, actor_name, request_id, created_at }],
    next_cursor }
```

Admin-only: `403` for a member, `404` for a non-member. Keyset pagination on `created_at` — an audit
view must not skip rows under concurrent writes, which `OFFSET` would.

Recorded actions: `document.upload`, `document.delete`, `document.reprocess`, `document.download`,
`workspace.delete`, `workspace.member.add`, `.remove`, `.leave`, `.role_change`.

`detail` is a JSON **object** built from a whitelist that contains no document or note text. `actor_id`
is `null` when the account has since been deleted, and the event remains.

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
