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

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "preview", "production"]


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
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_jwt_secret: str
    database_url: str

    # --- ai providers -------------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

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
    max_upload_bytes: int = 50 * 1024 * 1024
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
    cors_allowed_origins: str = "http://localhost:5173"
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0
    job_stale_after_seconds: int = 900
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

    @field_validator("pinned_embedding_dim")
    @classmethod
    def _dim_must_match_pinned_model(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        model = info.data.get("pinned_embedding_model")
        known = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}
        expected = known.get(model or "")
        if expected is not None and v != expected:
            raise ValueError(
                f"{model} produces {expected}-dim vectors but PINNED_EMBEDDING_DIM={v}"
            )
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
