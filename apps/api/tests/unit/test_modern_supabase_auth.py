"""Modern Supabase authentication must fail closed and never trust JWT headers."""

from __future__ import annotations

import base64
import time
from unittest.mock import patch

import httpx
import pytest
from app.core import jwks
from app.core.config import Settings
from app.core.errors import ProviderUnavailable, Unauthorized
from app.core.security import decode_token
from app.core.supabase_headers import storage_admin_headers
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from jose import jwt

URL = "https://project.supabase.co"
KID = "active-key"
SUB = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def clear_cache():
    jwks._cache.clear()
    jwks._failure_until.clear()
    yield
    jwks._cache.clear()
    jwks._failure_until.clear()


@pytest.fixture
def pair():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()

    def b64(number):
        return base64.urlsafe_b64encode(number.to_bytes(32, "big")).rstrip(b"=").decode()

    public = {
        "kid": KID,
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "use": "sig",
        "x": b64(numbers.x),
        "y": b64(numbers.y),
    }
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return pem, public


def modern_settings():
    return Settings.model_construct(supabase_url=URL, supabase_jwt_algorithm="ES256")


def signed(private, kid=KID, **overrides):
    claims = {
        "sub": SUB,
        "aud": "authenticated",
        "role": "authenticated",
        "iss": f"{URL}/auth/v1",
        "exp": int(time.time()) + 120,
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid})


def test_es256_token_uses_trusted_project_jwks(pair):
    private, public = pair
    with patch.object(jwks, "_fetch", return_value={KID: public}) as fetch:
        assert decode_token(signed(private), modern_settings()).id == SUB
        assert decode_token(signed(private), modern_settings()).id == SUB
    fetch.assert_called_once_with(f"{URL}/auth/v1/.well-known/jwks.json")


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "anon"},
        {"aud": None},
        {"iss": "https://evil.example/auth/v1"},
        {"iss": None},
        {"exp": None},
        {"exp": 1},
        {"sub": None},
        {"role": "service_role"},
        {"role": None},
    ],
)
def test_es256_rejects_bad_claims(pair, override):
    private, public = pair
    with patch.object(jwks, "_fetch", return_value={KID: public}), pytest.raises(Unauthorized):
        decode_token(signed(private, **override), modern_settings())


def test_unknown_kid_and_hs256_downgrade_are_rejected(pair):
    private, public = pair
    with patch.object(jwks, "_fetch", return_value={KID: public}) as fetch:
        with pytest.raises(Unauthorized):
            decode_token(signed(private, kid="unknown"), modern_settings())
        forged = jwt.encode({"sub": SUB, "aud": "authenticated"}, "secret", algorithm="HS256")
        with pytest.raises(Unauthorized):
            decode_token(forged, modern_settings())
    assert fetch.call_count == 1  # bad algorithms cannot trigger a JWKS fetch


def test_untrusted_jku_header_is_ignored(pair):
    private, public = pair
    claims = {
        "sub": SUB,
        "aud": "authenticated",
        "role": "authenticated",
        "iss": f"{URL}/auth/v1",
        "exp": int(time.time()) + 120,
    }
    token = jwt.encode(
        claims, private, algorithm="ES256", headers={"kid": KID, "jku": "https://evil.example/jwks"}
    )
    with patch.object(jwks, "_fetch", return_value={KID: public}) as fetch:
        assert decode_token(token, modern_settings()).id == SUB
    fetch.assert_called_once_with(f"{URL}/auth/v1/.well-known/jwks.json")


def test_forged_signature_and_malformed_public_key_are_rejected(pair):
    private, public = pair
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    with patch.object(jwks, "_fetch", return_value={KID: public}), pytest.raises(Unauthorized):
        decode_token(signed(other), modern_settings())
    jwks._cache.clear()
    with (
        patch.object(jwks, "_fetch", return_value={KID: {"kty": "EC"}}),
        pytest.raises(Unauthorized),
    ):
        decode_token(signed(private), modern_settings())


def test_expired_jwks_cache_fails_closed_on_network_error(pair, monkeypatch):
    private, public = pair
    with patch.object(jwks, "_fetch", return_value={KID: public}):
        assert decode_token(signed(private), modern_settings()).id == SUB
    now = jwks._cache[f"{URL}/auth/v1/.well-known/jwks.json"][0]
    monkeypatch.setattr(jwks.time, "monotonic", lambda: now + 61)
    with (
        patch.object(jwks, "_fetch", side_effect=ProviderUnavailable("unavailable")),
        pytest.raises(ProviderUnavailable),
    ):
        decode_token(signed(private), modern_settings())


def test_unknown_key_refresh_is_rate_limited_and_picks_up_rotation(pair, monkeypatch):
    _, public = pair
    url = f"{URL}/auth/v1/.well-known/jwks.json"
    clock = [100.0]
    monkeypatch.setattr(jwks.time, "monotonic", lambda: clock[0])
    with patch.object(jwks, "_fetch", side_effect=[{KID: public}, {"new": public}]) as fetch:
        jwks.signing_key(URL, KID)
        with pytest.raises(Unauthorized):
            jwks.signing_key(URL, "new")
        assert fetch.call_count == 1
        clock[0] += 11
        assert jwks.signing_key(URL, "new") == public
        assert fetch.call_count == 2
        assert url in jwks._cache


def test_jwks_fetch_rejects_bad_json_and_non_ec_keys(monkeypatch):
    def run(response):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
        monkeypatch.setattr(jwks.httpx, "stream", client.stream)
        return jwks._fetch(f"{URL}/auth/v1/.well-known/jwks.json")

    with pytest.raises(ProviderUnavailable):
        run(httpx.Response(200, text="not json"))
    assert run(httpx.Response(200, json={"keys": [{"kid": "bad", "kty": "oct"}]})) == {}


def test_modern_secret_is_not_sent_as_bearer():
    modern = Settings.model_construct(supabase_service_role_key="sb_secret_example")
    legacy = Settings.model_construct(supabase_service_role_key="legacy.jwt.key")
    assert storage_admin_headers(modern) == {"apikey": "sb_secret_example"}
    assert storage_admin_headers(legacy) == {
        "apikey": "legacy.jwt.key",
        "Authorization": "Bearer legacy.jwt.key",
    }


def test_modern_environment_works_without_legacy_jwt_secret():
    settings = Settings(
        _env_file=None,
        supabase_url=URL,
        supabase_anon_key="old-anon",
        supabase_service_role_key="old-service",
        supabase_publishable_key="sb_publishable_example",
        supabase_secret_key="sb_secret_example",
        supabase_jwt_algorithm="ES256",
        supabase_jwt_secret="",
        database_url="postgresql://localhost/test",
        openai_api_key="test",
    )
    assert settings.supabase_anon_key == "sb_publishable_example"
    assert settings.supabase_service_role_key == "sb_secret_example"
    assert settings.supabase_jwt_secret == ""


def test_hs256_rejects_empty_secret():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="SUPABASE_JWT_SECRET"):
        Settings(
            _env_file=None,
            supabase_url=URL,
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            supabase_jwt_algorithm="HS256",
            supabase_jwt_secret="",
            database_url="postgresql://localhost/test",
            openai_api_key="test",
        )


def test_production_es256_rejects_insecure_supabase_url():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="HTTPS SUPABASE_URL"):
        Settings(
            _env_file=None,
            environment="production",
            cors_allowed_origins="https://example.com",
            supabase_url="http://project.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            supabase_jwt_algorithm="ES256",
            supabase_jwt_secret="",
            database_url="postgresql://localhost/test",
            openai_api_key="test",
        )


def test_failed_fetch_is_rate_limited(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(jwks.time, "monotonic", lambda: clock[0])
    with patch.object(jwks, "_fetch", side_effect=ProviderUnavailable("unavailable")) as fetch:
        for _ in range(3):
            with pytest.raises(ProviderUnavailable):
                jwks.signing_key(URL, KID)
        assert fetch.call_count == 1
        clock[0] += 6
        with pytest.raises(ProviderUnavailable):
            jwks.signing_key(URL, KID)
        assert fetch.call_count == 2


def test_storage_signing_uses_new_key_in_apikey_header():
    from unittest.mock import MagicMock

    from app.services.storage_service import _sign

    settings = Settings.model_construct(
        supabase_url=URL,
        storage_bucket="documents",
        supabase_service_role_key="sb_secret_example",
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {"signedURL": "/test"}
    with patch("app.services.storage_service.httpx.post", return_value=response) as post:
        _sign(settings, "file.txt", 60)
    assert post.call_args.kwargs["headers"] == {"apikey": "sb_secret_example"}
