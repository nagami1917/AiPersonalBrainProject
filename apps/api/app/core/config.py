"""Single source of configuration truth.

Two rules enforced here:

1. Required secrets fail at startup, not at request time. A backend that boots
   without an embedding key would happily build an index nobody can search.
2. Model identity is pinned. Chunking/retrieval code reads the pinned embedding
   model and dimension from here and nowhere else (invariant 7).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "preview", "production"]

# ---------------------------------------------------------------------------
# The versioned API prefix — the single definition of where routes live.
# ---------------------------------------------------------------------------
# The application was originally mounted at `/v1` and the browser called
# `/api/v1`, with a proxy translating between them. Two spellings of the same
# thing is a bug waiting to be filed: every log line, doc example and test URL
# had to remember which side of the proxy it was on.
#
# The prefix is now `/api/v1` end to end. It is assembled from two constants
# because they mean different things — `/api` is the routing namespace,
# `/v1` is the version — and a future `/v2` should change only the second.
#
# `core/tenancy` and the frontend's `paths.ts` both derive their prefixes from
# this, so changing the version here changes it everywhere, including the
# OpenAPI document.
API_NAMESPACE = "/api"
API_VERSION = "v1"
API_PREFIX = f"{API_NAMESPACE}/{API_VERSION}"

# Known model → dimension. Pinned here because these two values are a pair: a
# vector column built for one is unusable with the other.
_EMBEDDING_DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}

# The documented local default. Named so the alias resolver can tell "the
# operator set this" from "this is the fallback" — a field with a non-empty
# default cannot be tested for presence by truthiness alone.
_DEFAULT_CORS = "http://localhost:5173"


def _assert_dim_matches(model: str, dim: int) -> int:
    """Return `dim` if it is consistent with `model`, else raise.

    Called from the field validator *and* from the alias resolver: a setting can
    arrive under either spelling, and checking only one path is how
    `EMBEDDING_MODEL=text-embedding-3-large` would slip through with dim=1536.
    """
    expected = _EMBEDDING_DIMS.get(model)
    if expected is not None and dim != expected:
        raise ValueError(f"{model} produces {expected}-dim vectors but PINNED_EMBEDDING_DIM={dim}")
    return dim


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime ------------------------------------------------------------
    environment: Environment = "local"
    log_level: str = "INFO"
    api_version: str = "0.1.0"

    # --- supabase -----------------------------------------------------------
    supabase_url: str
    # New API keys are opaque and independent of JWT signing keys. Legacy names
    # remain accepted for local Supabase and existing deployments.
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    # Explicit mode prevents a token header from choosing a weaker algorithm.
    supabase_jwt_algorithm: Literal["HS256", "ES256"] = "HS256"
    supabase_jwt_secret: str = ""
    database_url: str = ""
    # The spec's name for the same connection. Resolved into `database_url` by
    # the validator below, so no other module needs to know both exist.
    supabase_db_url: str = ""

    # --- ai providers -------------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # Spec names. `AI_API_KEY` / `AI_MODEL` / `EMBEDDING_MODEL` are folded into
    # the canonical fields by `_resolve_spec_aliases`; they exist so a value
    # pasted from the deployment document works instead of failing at boot.
    ai_api_key: str = ""
    ai_model: str = ""
    embedding_model: str = ""

    # Pinned model identity. Changing either value is a migration, not a tweak.
    pinned_embedding_model: str = "text-embedding-3-small"
    pinned_embedding_dim: int = 1536
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_output_tokens: int = 1200

    # --- retrieval tuning ---------------------------------------------------
    retrieval_k: int = 8
    candidate_k: int = 40
    vector_weight: float = 1.0
    keyword_weight: float = 0.7
    rrf_k: int = 60
    mmr_lambda: float = 0.7
    # Absolute relevance floor, applied to cosine similarity in SQL before a
    # candidate can enter the vector arm.
    #
    # Why similarity and not the fused RRF score: HNSW returns k neighbours for
    # ANY query vector, so a top-1 RRF score is ~constant regardless of
    # relevance. Only similarity can express "this is unrelated", which is what
    # the ungrounded decision needs. 0.25 is deliberately permissive; tune it
    # against the eval set's unanswerable questions, never by vibes.
    min_similarity: float = 0.25
    # Secondary guard: fused score floor for keyword-only hits.
    score_floor: float = 0.008
    max_context_tokens: int = 6000

    # --- limits / behaviour --------------------------------------------------
    max_upload_bytes: int = 0  # resolved from MAX_UPLOAD_SIZE_MB below if unset
    # The spec names the limit in megabytes. Bytes remain the canonical unit
    # because that is what Storage and the schemas use; this is the input form.
    max_upload_size_mb: int = 25
    # Design §4.1: PDF, DOCX, TXT, Markdown, HTML. `application/msword` is not
    # listed there — python-docx reads .docx only, so accepting .doc would fail
    # at extraction time with a confusing error instead of at upload time.
    allowed_mime_types: tuple[str, ...] = (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/html",
        "application/xhtml+xml",
    )
    cors_allowed_origins: str = _DEFAULT_CORS
    # Spec name for the same list.
    allowed_origins: str = ""
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0
    job_stale_after_seconds: int = 900
    # How long a deleted note or document stays restorable before purge removes
    # it for good. 30 days is the retention users expect from a "trash" feature;
    # 0 disables recovery (delete is immediate), which is a deliberate choice a
    # deployment can make rather than a default anyone falls into by accident.
    trash_retention_days: int = 30
    signed_upload_ttl_seconds: int = 300
    signed_download_ttl_seconds: int = 60
    storage_bucket: str = "nexus-documents"
    sentry_dsn: str = ""

    # --- rate limits (security §7, rule 8) -----------------------------------
    # Fixed window, counted per user AND per workspace. Sized so a person
    # chatting normally never notices: 30 AI calls/minute is two questions a
    # second for a minute, and a streaming answer occupies one slot for its
    # whole duration. The workspace ceiling is the shared-cost guard.
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: float = 60.0
    rate_limit_user_calls: int = 30
    rate_limit_workspace_calls: int = 240

    # --- upload content inspection (security §7, rule 9) ---------------------
    # Magic-byte sniffing reads this many bytes. Every supported format has a
    # signature within the first 512, and a document that does not is a
    # document we cannot classify — which is the point of checking.
    sniff_bytes: int = 4096

    @model_validator(mode="after")
    def _resolve_spec_aliases(self) -> Settings:
        """Fold the spec's variable names into the canonical fields.

        The deployment document and this codebase grew different names for the
        same settings (`SUPABASE_DB_URL` vs `DATABASE_URL`, `AI_API_KEY` vs
        `OPENAI_API_KEY`, `ALLOWED_ORIGINS` vs `CORS_ALLOWED_ORIGINS`,
        `MAX_UPLOAD_SIZE_MB` vs `MAX_UPLOAD_BYTES`).

        Accepting both is not laziness: a renamed variable in a Fly secret or a
        Vercel project fails at boot with "missing required value", which reads
        as a broken deployment rather than a spelling mismatch. The canonical
        name always wins when both are set, so behaviour never depends on which
        one happens to be present.

        Each alias is stated in the comment where it is resolved, and listed in
        DEPLOYMENT.md §5, so the accepted set is discoverable rather than
        folklore.
        """
        # New API keys take precedence when both generations are configured.
        if self.supabase_publishable_key:
            self.supabase_anon_key = self.supabase_publishable_key
        if self.supabase_secret_key:
            self.supabase_service_role_key = self.supabase_secret_key
        if not self.supabase_anon_key:
            raise ValueError("SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY is required")
        if not self.supabase_service_role_key:
            raise ValueError("SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is required")
        if self.supabase_jwt_algorithm == "HS256" and not self.supabase_jwt_secret:
            raise ValueError("SUPABASE_JWT_SECRET is required for HS256")
        if self.supabase_jwt_algorithm == "ES256" and self.is_production:
            from urllib.parse import urlsplit

            url = urlsplit(self.supabase_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("ES256 production verification requires an HTTPS SUPABASE_URL")

        # SUPABASE_DB_URL → DATABASE_URL
        if not self.database_url and self.supabase_db_url:
            self.database_url = self.supabase_db_url
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL (or SUPABASE_DB_URL) is required — the API cannot "
                "reach Postgres without it"
            )

        # AI_API_KEY → OPENAI_API_KEY
        if not self.openai_api_key and self.ai_api_key:
            self.openai_api_key = self.ai_api_key
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY (or AI_API_KEY) is required")
        # AI_MODEL → LLM_MODEL, EMBEDDING_MODEL → PINNED_EMBEDDING_MODEL
        if self.ai_model:
            self.llm_model = self.ai_model
        if self.embedding_model:
            self.pinned_embedding_model = self.embedding_model

        # ALLOWED_ORIGINS → CORS_ALLOWED_ORIGINS
        #
        # Guarded by "is the canonical value still the default?", because the
        # field has a non-empty default: `if self.allowed_origins` alone would
        # let the alias silently override an explicitly configured
        # CORS_ALLOWED_ORIGINS. Every other alias folds into a field that
        # defaults to empty or zero, where a bare truthiness test is enough.
        if self.allowed_origins and self.cors_allowed_origins == _DEFAULT_CORS:
            self.cors_allowed_origins = self.allowed_origins

        # A production deployment that kept the development CORS default would
        # reject the browser's preflight while looking configured. Supabase auth
        # itself is not CORS-bound (the SDK calls the Supabase origin, which
        # sends its own headers), so this fails only once the frontend calls the
        # API — the least obvious possible moment. Refuse at boot instead.
        if self.environment == "production" and self.cors_allowed_origins == _DEFAULT_CORS:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS is still the development default "
                f"({_DEFAULT_CORS}). Set it to the deployed frontend origin, "
                "e.g. CORS_ALLOWED_ORIGINS=https://your-app.vercel.app"
            )

        # MAX_UPLOAD_SIZE_MB → MAX_UPLOAD_BYTES (bytes win when both are set)
        if not self.max_upload_bytes:
            self.max_upload_bytes = self.max_upload_size_mb * 1024 * 1024

        # The embedding model and its dimension are a pair: a vector column built
        # for one is unusable with the other. Checked here, once, after every
        # spelling has been folded in — a field-level validator would compare
        # against whichever model was the default rather than the configured one,
        # and would reject the valid `EMBEDDING_MODEL=…-large` + dim=3072 pair.
        self.pinned_embedding_dim = _assert_dim_matches(
            self.pinned_embedding_model, self.pinned_embedding_dim
        )

        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
