"""Audit detail whitelisting.

Rule 10 of the security architecture asks for audit information; rule 7 forbids
logging private contents. The two collide exactly at `audit_events.detail`, which
is the most tempting place in the codebase to write `row_to_json(document)`.

The whitelist is the answer, and these tests are what keep it a whitelist. A
single editor adding "content" to `SAFE_FIELDS` to debug something would put
every document body in a permanently retained, admin-readable table; the test
below is what fails first.
"""

from __future__ import annotations

import pytest
from app.core.audit import SAFE_FIELDS, scrub

FORBIDDEN = ("content", "raw_text", "body", "text", "summary", "answer", "question", "prompt")


class TestWhitelist:
    @pytest.mark.parametrize("field", FORBIDDEN)
    def test_document_bodies_are_never_allowed(self, field):
        """The one invariant that must not regress."""
        assert field not in SAFE_FIELDS, (
            f"{field!r} in SAFE_FIELDS would write private text into the audit "
            "trail, which admins can read and nothing prunes"
        )

    @pytest.mark.parametrize("field", FORBIDDEN)
    def test_bodies_are_dropped_at_write_time(self, field):
        assert scrub({field: "private words", "title": "kept"}) == {"title": "kept"}

    def test_metadata_is_kept(self):
        detail = {"role": "member", "email": "a@b.c", "file_size": 12}
        assert scrub(detail) == detail

    def test_unknown_fields_are_dropped(self):
        assert scrub({"something_new": 1}) == {}

    def test_nested_structures_are_dropped(self):
        """A dict could smuggle anything past a key-name check."""
        assert scrub({"title": {"nested": "text"}}) == {}
        assert scrub({"title": ["a", "b"]}) == {}

    def test_long_strings_are_truncated(self):
        out = scrub({"title": "x" * 500})
        assert len(out["title"]) == 200

    def test_numeric_values_survive(self):
        assert scrub({"count": 3, "tokens": 0.5}) == {"count": 3, "tokens": 0.5}

    def test_none_is_preserved(self):
        """`None` is meaningful: it says 'not applicable', which {}.get would hide."""
        assert scrub({"collection_id": None}) == {"collection_id": None}

    def test_empty_input(self):
        assert scrub(None) == {}
        assert scrub({}) == {}


class TestRecordNeverRaises:
    async def test_a_broken_connection_does_not_fail_the_operation(self):
        """An audit write failing must not roll back the user's successful action.

        The alternative — delete succeeded, audit failed, request 500s — tells the
        user their delete did not happen when it did.
        """
        from app.core import audit

        class Broken:
            async def fetchval(self, *args, **kwargs):
                raise RuntimeError("connection gone")

        # No exception escapes.
        await audit.record(Broken(), workspace_id="w", action="document.delete")
