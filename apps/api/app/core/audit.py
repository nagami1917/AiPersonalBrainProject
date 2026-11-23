"""Writing the audit trail.

Rule 10 of the security architecture: keep audit information for sensitive
workspace operations. The table and the `record_audit` function live in
migration 0013; this module is the only writer.

## The rule that shapes everything here

Rule 7's other half says never log private document contents unnecessarily. An
audit table is *exactly* where that rule gets broken, because the tempting
implementation is `detail=row_to_json(document)`. That would write every
document's raw text, every note body, and every chat message into a table that
admins can read and nothing ever prunes.

So `detail` is built from a **whitelist** (`SAFE_FIELDS`). A caller passing
`content` or `raw_text` gets those keys dropped silently rather than trusted to
have remembered. Auditing what changed is the job; storing what was written is
not.

## Failures must not fail the operation

`record` swallows its own errors and logs them. The alternative — a failed audit
insert rolling back a successful delete — inverts the priority: the user's
intent succeeded, and losing the log entry is the smaller harm. It is logged at
ERROR so a silently broken audit trail is visible in logs, which is where
someone would look.

## …but swallowing the exception is not enough, and used not to be

Catching the error keeps the *route* from raising; it does not keep the
*transaction* from being poisoned. In Postgres a failed statement aborts the
whole transaction, and every later statement returns `25P02` until it is rolled
back. So the old implementation produced the worst possible outcome:

    DELETE 1                    -- the user's operation succeeded
    select record_audit(...)    -- raises, caught, logged
    COMMIT                      -- issues ROLLBACK: the transaction is aborted
    -> HTTP 204                 -- the client is told it worked

Neither outcome the docstring promised. It was tested end to end through
`DELETE /workspaces/{id}/members/{me}`: the route returned 204 and the
membership row was still there afterwards. Every audited route shared the bug,
because all of them call `record` as their last statement.

`record` now runs the insert inside a SAVEPOINT. A failure rolls back to the
savepoint, which clears the aborted state while leaving everything the route did
before it intact — so the operation really does commit, and the only thing lost
is the log entry. Two cases are deliberately *not* swallowed:

  * no connection / no transaction (`asyncpg.InterfaceError`,
    `asyncpg.NoActiveSQLTransactionError`) — caller error, and silently skipping
    the audit would hide it;
  * a connection that has already gone away (`ConnectionDoesNotExistError`) —
    there is no transaction left to save.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

log = logging.getLogger("nexus.audit")

# Anything not in here is dropped. Deliberately excludes every free-text body:
# content, raw_text, summary, message, answer, question, body, prompt.
SAFE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "filename",
        "role",
        "email",
        "count",
        "kind",
        "status",
        "previous_role",
        "new_role",
        "previous_title",
        "title_before",
        "title_after",
        "fields_changed",
        "link_count",
        "reason",
        "chunks",
        "tokens",
        "document_id",
        "note_id",
        "collection_id",
        "parent_id",
        "conversation_id",
        "member_id",
        "depth",
        "file_size",
        "mime_type",
    }
)

_MAX_VALUE_CHARS = 200


def scrub(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only whitelisted, bounded values.

    Truncation is per value rather than dropping long values, because a 300
    character title is still a title — but the field name has to be allowlisted
    before it is considered at all.
    """
    if not detail:
        return {}
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in SAFE_FIELDS:
            continue
        if isinstance(value, str):
            clean[key] = value[:_MAX_VALUE_CHARS]
        elif isinstance(value, int | float | bool) or value is None:
            clean[key] = value
        # dicts/lists are dropped: a nested writer could smuggle anything in.
    return clean


async def record(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    """Append one audit entry. Never poisons the caller's transaction.

    `conn` must be the caller's user connection: `record_audit` derives the
    actor from the JWT claim on that session, so a service-role connection would
    have no actor and the function would refuse.

    Wrapped in a SAVEPOINT so that a refused audit entry costs the log line and
    nothing else. Without it, catching the exception is not sufficient: the
    transaction stays aborted and `COMMIT` silently becomes a rollback, which is
    how a 204 used to be returned for a delete that did not happen.
    """
    try:
        async with conn.transaction():
            await conn.fetchval(
                """select public.record_audit(
                     p_workspace_id => $1::uuid,
                     p_action       => $2,
                     p_target_type  => $3,
                     p_target_id    => $4::uuid,
                     p_target_label => $5,
                     p_detail       => $6::jsonb,
                     p_request_id   => $7)""",
                workspace_id,
                action,
                target_type,
                target_id,
                target_label,
                # The dict, not json.dumps(dict). asyncpg encodes a `dict` for a
                # jsonb parameter as a JSON *object*; handing it a pre-serialised
                # string makes it a JSON string, so `detail` came back as "{}" and
                # every reader would have to parse it again.
                scrub(detail),
                request_id,
            )
    except Exception:
        # Deliberately broad: a broken audit insert must not break the request,
        # and any narrower catch would let an unexpected error escape. The
        # savepoint has already unwound the failed statement, so the caller's
        # transaction is usable again by the time this returns.
        log.exception(
            "audit write failed",
            extra={"workspace_id": workspace_id, "action": action},
        )
