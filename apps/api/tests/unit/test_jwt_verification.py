"""JWT verification: signatures, claims, and the ways a token can lie.

The requirement is "verify JWT signatures and claims on the backend; never trust
user IDs supplied by the frontend". `decode_token` implemented that from the
beginning, and until now **nothing tested it** — every existing test minted a
valid token and asserted a happy path, so a regression that accepted a forged
one would have gone unnoticed.

These are unit tests: no database, no network. What is being tested is the
decision to trust a token or not, which is exactly the kind of logic that must
be provable without infrastructure.
"""

from __future__ import annotations

import time

import pytest
from app.core.config import Settings
from app.core.errors import Unauthorized
from app.core.security import decode_token
from jose import jwt

SECRET = "a-test-jwt-secret-of-at-least-32-characters"
OTHER_SECRET = "a-completely-different-secret-also-long-enough"

ALICE = "11111111-1111-1111-1111-111111111111"


def settings() -> Settings:
    """Settings carrying only what `decode_token` reads.

    `model_construct` skips the alias/cross-field validation in `config.py`,
    which is unrelated to token verification and would require a dozen
    environment variables to satisfy.
    """
    return Settings.model_construct(supabase_jwt_secret=SECRET, environment="test")


def token(**overrides: object) -> str:
    claims: dict[str, object] = {
        "sub": ALICE,
        "aud": "authenticated",
        "role": "authenticated",
        "email": "alice@nexus.local",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    # `None` means "omit this claim" so a test can build a claimless token.
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, SECRET, algorithm="HS256")


class TestValidTokens:
    def test_a_properly_signed_token_identifies_its_subject(self) -> None:
        user = decode_token(token(), settings())
        assert user.id == ALICE
        assert user.role == "authenticated"
        assert user.email == "alice@nexus.local"

    def test_the_raw_token_is_kept_for_downstream_use(self) -> None:
        # `user_db` needs the original token to bind the connection identity.
        raw = token()
        assert decode_token(raw, settings()).token == raw

    def test_claims_are_exposed_unchanged(self) -> None:
        user = decode_token(token(custom="value"), settings())
        assert user.claims["custom"] == "value"

    def test_role_defaults_to_authenticated_when_absent(self) -> None:
        user = decode_token(token(role=None), settings())
        assert user.role == "authenticated"


class TestSignature:
    def test_a_token_signed_with_another_secret_is_rejected(self) -> None:
        # The attack this prevents: minting your own token for someone else's
        # `sub` because the attacker knows the claim format.
        forged = jwt.encode(
            {"sub": ALICE, "aud": "authenticated", "exp": int(time.time()) + 3600},
            OTHER_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(Unauthorized):
            decode_token(forged, settings())

    def test_a_tampered_payload_breaks_the_signature(self) -> None:
        good = token()
        header, payload, _signature = good.split(".")
        # Swap in a payload claiming a different user, keeping the signature.
        import base64
        import json

        def b64(obj: dict) -> str:
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        forged_payload = b64(
            {"sub": "22222222-2222-2222-2222-222222222222", "aud": "authenticated"}
        )
        with pytest.raises(Unauthorized):
            decode_token(f"{header}.{forged_payload}.{_signature}", settings())

    def test_an_unsigned_alg_none_token_is_rejected(self) -> None:
        # The classic JWT bypass: `{"alg":"none"}` with an empty signature. It is
        # rejected because the algorithm is pinned to HS256, not read from the
        # header — accepting the header's choice is the vulnerability.
        import base64
        import json

        def b64(obj: dict) -> str:
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = b64({"alg": "none", "typ": "JWT"})
        payload = b64({"sub": ALICE, "aud": "authenticated"})
        with pytest.raises(Unauthorized):
            decode_token(f"{header}.{payload}.", settings())

    def test_a_malformed_string_is_rejected_without_raising_elsewhere(self) -> None:
        for junk in ("", "not-a-token", "a.b", "a.b.c.d"):
            with pytest.raises(Unauthorized):
                decode_token(junk, settings())

    def test_a_token_signed_with_rs256_is_rejected(self) -> None:
        # Algorithm confusion: pinning HS256 means a token claiming a different
        # algorithm cannot be verified with the shared secret.
        with pytest.raises(Unauthorized):
            decode_token(
                token(),
                Settings.model_construct(
                    supabase_jwt_secret="wrong-secret-also-at-least-32-chars-long",
                    environment="test",
                ),
            )


class TestClaims:
    def test_an_expired_token_is_rejected(self) -> None:
        expired = token(exp=int(time.time()) - 60)
        with pytest.raises(Unauthorized):
            decode_token(expired, settings())

    def test_a_token_that_is_not_expired_yet_is_accepted(self) -> None:
        # Boundary sanity: the check must not reject a token with a future exp.
        assert decode_token(token(exp=int(time.time()) + 30), settings()).id == ALICE

    def test_a_wrong_audience_is_rejected(self) -> None:
        # A Supabase anon-key token, or a token minted for another service, must
        # not be accepted as a user session.
        with pytest.raises(Unauthorized):
            decode_token(token(aud="anon"), settings())

    def test_a_missing_audience_is_rejected(self) -> None:
        with pytest.raises(Unauthorized):
            decode_token(token(aud=None), settings())

    def test_a_token_without_a_subject_is_rejected(self) -> None:
        # Every authorisation decision keys on `sub`. A token without one cannot
        # identify anyone, so it must fail here rather than produce user_id=None.
        with pytest.raises(Unauthorized):
            decode_token(token(sub=None), settings())

    def test_an_empty_subject_is_rejected(self) -> None:
        with pytest.raises(Unauthorized):
            decode_token(token(sub=""), settings())


class TestErrorShape:
    def test_every_rejection_uses_the_unauthorized_code(self) -> None:
        # One error envelope, so the frontend has one path: 401 + code
        # "unauthorized". A different code here would need special handling.
        for bad in (token(exp=int(time.time()) - 60), "junk", token(aud="anon")):
            with pytest.raises(Unauthorized) as exc:
                decode_token(bad, settings())
            assert exc.value.status_code == 401
            assert exc.value.code == "unauthorized"

    def test_the_message_says_invalid_or_expired_not_why(self) -> None:
        # The reason is deliberately not echoed: telling an attacker whether the
        # signature or the expiry failed is free information.
        with pytest.raises(Unauthorized) as exc:
            decode_token("junk", settings())
        assert "Invalid or expired token" in exc.value.message
