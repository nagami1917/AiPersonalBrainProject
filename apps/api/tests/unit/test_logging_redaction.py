"""Log redaction.

Rule 7 of the security architecture: never log secrets or full private document
contents unnecessarily.

A secret in a log line is not a small mistake. Logs are copied into support
tickets, shipped to aggregators, and retained far longer than the credential
they exposed — so a leaked service-role key in a log is a permanent compromise
of every tenant, discovered long after the rotation that should have fixed it.

Two mechanisms are tested separately because they fail differently: the
formatter's redaction catches secrets already inside a message, and the field
whitelist prevents a whole request object being serialised.
"""

from __future__ import annotations

import json
import logging

import pytest
from app.core.logging import JsonFormatter, redact

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)


class TestPatterns:
    def test_a_bearer_token_is_redacted(self):
        out = redact(f"Authorization: Bearer {JWT}")
        assert JWT not in out
        assert "[redacted]" in out

    def test_a_raw_jwt_is_redacted_even_outside_a_header(self):
        assert JWT not in redact(f"token was {JWT} oops")

    def test_a_supabase_secret_key_is_redacted(self):
        out = redact("using sb_secret_abcdefghijklmnop1234 for this call")
        assert "sb_secret_abcdefghijklmnop1234" not in out

    def test_an_openai_key_is_redacted(self):
        assert "sk-proj-abcdefghijklmnopqrstuv" not in redact(
            "key sk-proj-abcdefghijklmnopqrstuv failed"
        )

    def test_a_signed_storage_url_token_is_redacted(self):
        """A signed URL *is* a working download link until it expires.

        This is the case a naive "no API keys in logs" rule misses: nothing here
        looks like a key, but the logged URL grants file access.
        """
        url = "https://x.supabase.co/storage/v1/object/sign/nexus-documents/a/b.pdf?token=abcdef123456"
        out = redact(url)
        assert "abcdef123456" not in out
        assert "?token=[redacted]" in out

    @pytest.mark.parametrize(
        "message",
        [
            "password=hunter2",
            "passwd: hunter2",
            "secret = hunter2",
            "api_key: hunter2",
            "apiKey=hunter2",
            "access_token: hunter2",
            "service_role_key=hunter2",
        ],
    )
    def test_explicit_assignments_are_redacted(self, message):
        out = redact(message)
        assert "hunter2" not in out

    def test_ordinary_text_is_untouched(self):
        """Over-redaction destroys the logs this project relies on for debugging."""
        message = "document 7 processed in 1200ms with 42 chunks"
        assert redact(message) == message

    def test_a_word_containing_key_is_not_redacted(self):
        assert redact("keyboard shortcut") == "keyboard shortcut"


class TestFormatter:
    """The formatter is where redaction is actually applied."""

    def _format(self, message: str, **extra) -> dict:
        record = logging.LogRecord(
            name="nexus.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=None,
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return json.loads(JsonFormatter().format(record))

    def test_the_message_is_redacted_when_interpolated(self):
        """A logger that formatted the secret itself must still be covered."""
        payload = self._format(f"auth failed for bearer {JWT}")
        assert JWT not in payload["msg"]

    def test_only_whitelisted_extra_fields_survive(self):
        """A handler cannot accidentally serialise an arbitrary object.

        This is what stops `log.info("x", extra={"document": row})` from writing
        a document's full text into the log.
        """
        payload = self._format("ok", request_id="req_1", document={"raw_text": "private"})
        assert payload["request_id"] == "req_1"
        assert "document" not in payload

    def test_standard_fields_are_present(self):
        payload = self._format("ok", status=200, path="/api/v1/x")
        assert payload["msg"] == "ok"
        assert payload["status"] == 200
        assert payload["level"] == "INFO"

    def test_a_traceback_is_redacted_too(self):
        """Tracebacks quote request URLs and local values, so they leak the same way."""
        try:
            raise RuntimeError(f"failed with token {JWT}")
        except RuntimeError:
            import sys

            record = logging.LogRecord(
                name="nexus.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="boom",
                args=None,
                exc_info=sys.exc_info(),
            )
        payload = json.loads(JsonFormatter().format(record))
        assert JWT not in payload["exc"]
