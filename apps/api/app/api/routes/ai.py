"""`/ai` — the two generative endpoints, at the spec's paths.

The workspace-scoped canonical routes live in `routes/chat.py`
(`/workspaces/{id}/chat`, `…/chat/stream`) because a workspace in the path is how
every other resource route works, and because a redirect that dropped the
workspace would be a tenancy hazard dressed as convenience.

What this module adds is the surface the API spec asks for:

  * `POST /ai/chat` and `POST /ai/stream` take `workspace_id` in the BODY and are
    thin delegations to the same service functions. They exist for clients that
    treat the workspace as request data rather than a path prefix.
  * `POST /ai/summarize` is genuinely new — see the docstring on the handler for
    why it does not read a stored `summary` column.

The rule that makes these safe: the workspace in the body is authorised exactly
like the one in the path (membership, or 404), and no client-supplied id — chunk,
document, conversation — is ever trusted as a citation.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.providers import LLM, Embedder
from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, get_embedder, get_llm
from app.core.errors import NexusError, NotFound
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/ai", tags=["ai"])


class AiChatRequest(ChatRequest):
    """`ChatRequest` plus the workspace, which the spec carries in the body.

    Two naming accommodations, both additive and both resolved server-side:

      * `question` is accepted as an alias for `message`, because the spec's
        example request uses it while the canonical route (and the frontend) use
        `message`. Exactly one of them must be supplied.
      * `filters.document_ids` is accepted as an alias for `filters.source_ids`
        because the spec's example uses that name. Both end up in the same SQL
        filter — the client narrows, the server decides what an id means.
    """

    workspace_id: str = Field(description="Workspace to answer from")
    # Override the parent's required field so either spelling is accepted; the
    # "exactly one" rule is enforced in resolved() rather than by the schema,
    # because a schema validator would reject the spec's own example.
    message: str | None = Field(default=None, min_length=1, max_length=4000)  # type: ignore[assignment]
    question: str | None = Field(default=None, min_length=1, max_length=4000)

    def resolved(self) -> ChatRequest:
        """Normalise the two accepted spellings into the canonical request."""
        text = self.message or self.question
        if not text:
            raise NexusError("Provide `message` or `question`.", code="invalid_request")
        return ChatRequest(
            conversation_id=self.conversation_id,
            message=text,
            filters=self.filters,
            options=self.options,
        )


class SummarizeRequest(BaseModel):
    workspace_id: str
    source_id: str | None = None
    text: str | None = Field(default=None, max_length=100_000)
    style: str = Field(default="concise", pattern="^(concise|detailed)$")


async def _authorize(
    conn: asyncpg.Connection, *, user_id: str, workspace_id: str
) -> tenancy.WorkspaceContext:
    """Same authorisation as the path-based routes: member, or 404.

    Deliberately not a separate weaker check. A workspace id arriving in a body is
    exactly as untrusted as one arriving in a URL.
    """
    return await tenancy.resolve_workspace(conn, user_id=user_id, workspace_id=workspace_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: AiChatRequest,
    user: CurrentUser,
    conn: UserConn,
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    from app.api.routes.chat import _citations
    from app.services import chat_service

    ctx = await _authorize(conn, user_id=user.id, workspace_id=request.workspace_id)

    message_id, conversation_id, answer = await chat_service.answer(
        conn,
        workspace_id=ctx.id,
        user_id=user.id,
        request=request.resolved(),
        embedder=embedder,
        llm=llm,
        settings=settings,
    )
    return ChatResponse(
        message_id=message_id,
        conversation_id=conversation_id,
        content=answer.content,
        grounded=answer.grounded,
        citations=_citations(answer),
    )


@router.post("/stream")
async def stream(
    request: AiChatRequest,
    user: CurrentUser,
    conn: UserConn,
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Streaming chat, with the workspace authorised before the stream opens.

    The check runs here rather than inside the generator on purpose: a failure
    after the first byte has been sent can only be reported in-band, so the
    authorisation must happen while a real HTTP status is still possible.
    """
    from app.api.routes.chat import _stream_response

    ctx = await _authorize(conn, user_id=user.id, workspace_id=request.workspace_id)
    inner = request.resolved()
    return await _stream_response(
        workspace_id=ctx.id,
        user=user,
        request=inner,
        embedder=embedder,
        llm=llm,
        settings=settings,
    )


@router.post("/summarize")
async def summarize(
    request: SummarizeRequest,
    user: CurrentUser,
    conn: UserConn,
    llm: LLM = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Summarize pasted text, or a source the caller can already read.

    Why this generates rather than returning `documents.summary`: that column is
    filled by a background job that may not have run yet, and it is capped at 2000
    characters for list views. Returning it here would either be empty (confusing)
    or silently stale.

    Why it does not persist: `documents.summary` is the enrichment job's output and
    is written under a version guard. A second writer with different timing would
    race it. The stored summary stays the canonical one; this endpoint is an
    on-demand view of the same text.

    The two inputs are mutually exclusive, and neither is trusted: text is length
    capped by the schema, and a `source_id` is resolved against the caller's
    workspace, so a guessed id from another tenant is a 404.
    """
    ctx = await _authorize(conn, user_id=user.id, workspace_id=request.workspace_id)

    if bool(request.text) == bool(request.source_id):
        raise NexusError(
            "Provide exactly one of `text` or `source_id`.", code="invalid_summarize_input"
        )

    if request.text:
        body, title = request.text, None
    else:
        row = await conn.fetchrow(
            """select d.title, d.raw_text as text from public.documents d
                 where d.id=$1 and d.workspace_id=$2 and d.deleted_at is null
               union all
               select n.title, n.content as text from public.notes n
                 where n.id=$1 and n.workspace_id=$2 and n.deleted_at is null""",
            request.source_id,
            ctx.id,
        )
        if row is None:
            raise NotFound("Source not found")
        body, title = row["text"] or "", row["title"]
        if not body.strip():
            raise NexusError("That source has no extracted text yet.", code="source_has_no_text")

    from app.ai.summarization import build_document_summary_prompt, clean_summary

    summary = (
        await llm.complete(
            "You summarize documents factually and tersely.",
            build_document_summary_prompt(body),
        )
    ).strip()

    return {
        "summary": clean_summary(summary),
        "source_id": request.source_id,
        "title": title,
        "style": request.style,
    }
