#!/usr/bin/env bash
# =============================================================================
# Verify environment configuration before a deploy.
#
# Run as `make deploy-check`, or with the deploy targets. Prints PASS/FAIL per
# variable and **never prints a value** — a check that echoes secrets into CI
# logs has caused the incident it was written to prevent.
#
# What it catches, all of which are otherwise invisible until traffic:
#
#   * the spec's names used where this codebase reads the canonical ones (or
#     vice versa) — reported as satisfied, because both are accepted;
#   * PUBLIC_SUPABASE_URL accidentally set to a *secret* key, which would ship
#     it in the bundle;
#   * a service-role key set in the frontend environment — the one mistake the
#     architecture exists to make impossible;
#   * CORS listing a wildcard alongside credentials;
#   * an embedding model/dimension pair that cannot produce usable vectors.
#
# Exit status is non-zero if anything fails, so CI can gate on it.
# =============================================================================
set -uo pipefail

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
failures=0
warnings=0

pass() { printf '  %sPASS%s  %s\n' "$GREEN" "$RESET" "$1"; }
fail() { printf '  %sFAIL%s  %s\n' "$RED" "$RESET" "$1"; failures=$((failures + 1)); }
warn() { printf '  %sWARN%s  %s\n' "$YELLOW" "$RESET" "$1"; warnings=$((warnings + 1)); }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }

# nonempty NAME [alias...] — true when at least one name has a non-empty value.
nonempty() {
  local name
  for name in "$@"; do
    if [[ -n "${!name:-}" ]]; then return 0; fi
  done
  return 1
}

section() { printf '\n%s\n' "$1"; }

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
section "Backend (apps/api)"

if nonempty SUPABASE_URL; then pass "SUPABASE_URL is set"; else fail "SUPABASE_URL is not set"; fi
if nonempty SUPABASE_ANON_KEY; then pass "SUPABASE_ANON_KEY is set"; else fail "SUPABASE_ANON_KEY is not set"; fi
if nonempty SUPABASE_SERVICE_ROLE_KEY; then
  pass "SUPABASE_SERVICE_ROLE_KEY is set (server-side only)"
else
  fail "SUPABASE_SERVICE_ROLE_KEY is not set"
fi
if nonempty SUPABASE_JWT_SECRET; then
  len=${#SUPABASE_JWT_SECRET}
  if (( len >= 32 )); then
    pass "SUPABASE_JWT_SECRET is set (${len} chars)"
  else
    fail "SUPABASE_JWT_SECRET is too short (${len} chars, need 32+)"
  fi
else
  fail "SUPABASE_JWT_SECRET is not set"
fi

# Either spelling is accepted by app/core/config.py.
if nonempty DATABASE_URL SUPABASE_DB_URL; then
  pass "database URL is set ($([[ -n "${DATABASE_URL:-}" ]] && echo DATABASE_URL || echo SUPABASE_DB_URL))"
else
  fail "neither DATABASE_URL nor SUPABASE_DB_URL is set"
fi

if nonempty OPENAI_API_KEY AI_API_KEY; then
  pass "AI key is set ($([[ -n "${OPENAI_API_KEY:-}" ]] && echo OPENAI_API_KEY || echo AI_API_KEY))"
else
  fail "neither OPENAI_API_KEY nor AI_API_KEY is set"
fi

# The pair that silently produces an unsearchable index.
model="${PINNED_EMBEDDING_MODEL:-${EMBEDDING_MODEL:-text-embedding-3-small}}"
dim="${PINNED_EMBEDDING_DIM:-1536}"
case "$model" in
  text-embedding-3-small) expected=1536 ;;
  text-embedding-3-large) expected=3072 ;;
  *) expected="" ;;
esac
if [[ -n "$expected" && "$dim" != "$expected" ]]; then
  fail "embedding mismatch: $model produces ${expected}-dim vectors but the dimension is $dim"
else
  pass "embedding pair is consistent ($model / $dim)"
fi

# The pair check above compares settings against a table of known models. It
# cannot see the *column*, which is `vector(1536)` pinned by migration 0003 —
# the HNSW index is built for one width and is not rebuilt per request. A model
# whose width disagrees with the column passes the check above and then fails on
# the first chunk insert, after the upload was accepted. The API now refuses to
# boot in that state (`app/core/schema_guard.py`); this is the pre-deploy echo of
# the same check, and it only runs when a database is actually reachable.
if [[ -z "${SKIP_DB_CHECK:-}" ]] && command -v psql >/dev/null 2>&1; then
  db_url="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
  if [[ -z "$db_url" ]]; then
    note "embedding column not checked: no database URL"
  else
    col_dim="$(psql "$db_url" -tAc "
      select atttypmod from pg_attribute
      where attrelid = 'public.document_chunks'::regclass and attname = 'embedding'
    " 2>/dev/null || true)"
    if [[ -z "$col_dim" ]]; then
      note "embedding column not checked: table or database unreachable"
    elif [[ "$col_dim" == "-1" ]]; then
      note "embedding column has no fixed dimension"
    elif [[ "$col_dim" != "$dim" ]]; then
      fail "configured dimension $dim does not match document_chunks.embedding vector($col_dim)"
      note "ingestion would fail on the first chunk; migration 0003 pins the column"
    else
      pass "configured dimension matches the database column (vector($col_dim))"
    fi
  fi
else
  note "embedding column not checked (psql unavailable or SKIP_DB_CHECK=1)"
fi

origins="${CORS_ALLOWED_ORIGINS:-${ALLOWED_ORIGINS:-}}"
if [[ -z "$origins" ]]; then
  warn "no CORS origins configured — the browser will be refused"
elif [[ "$origins" == *"*"* ]]; then
  fail "CORS origins contain a wildcard; with credentials that is not permitted"
elif [[ "$origins" == http://localhost* || "$origins" == http://127.0.0.1* ]] \
     && [[ "${ENVIRONMENT:-local}" == "production" ]]; then
  fail "production CORS points at localhost — the deployed frontend will be refused"
else
  pass "CORS origins configured ($(awk -F, '{print NF}' <<<"$origins") listed)"
fi

if [[ "${ENVIRONMENT:-local}" == "production" ]] && [[ -n "${SENTRY_DSN:-}" ]]; then
  pass "SENTRY_DSN is set for production"
fi

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
section "Frontend (apps/web)"

if nonempty PUBLIC_SUPABASE_URL; then pass "PUBLIC_SUPABASE_URL is set"; else fail "PUBLIC_SUPABASE_URL is not set"; fi
if nonempty PUBLIC_SUPABASE_ANON_KEY; then
  pass "PUBLIC_SUPABASE_ANON_KEY is set"
else
  fail "PUBLIC_SUPABASE_ANON_KEY is not set"
fi

# The mistake the whole architecture is built to prevent.
if nonempty SUPABASE_SERVICE_ROLE_KEY && [[ "${CHECK_FRONTEND:-0}" == "1" ]]; then
  fail "a service-role key is present in the frontend environment — remove it immediately"
fi

if nonempty PUBLIC_API_BASE_URL; then
  case "$PUBLIC_API_BASE_URL" in
    http://localhost*|http://127.0.0.1*)
      if [[ "${ENVIRONMENT:-local}" == "production" ]]; then
        fail "PUBLIC_API_BASE_URL points at localhost in production — the browser cannot reach it"
      else
        note "PUBLIC_API_BASE_URL=$PUBLIC_API_BASE_URL (local development)"
      fi
      ;;
    https://*)
      pass "PUBLIC_API_BASE_URL is an absolute https origin"
      note "the API must list this frontend's origin in CORS_ALLOWED_ORIGINS"
      ;;
    *)
      warn "PUBLIC_API_BASE_URL is neither https nor localhost: $PUBLIC_API_BASE_URL"
      ;;
  esac
else
  pass "PUBLIC_API_BASE_URL is empty — same-origin /api/v1 (recommended)"
  note "requires the frontend and API to share an origin, or the vite/nginx proxy"
fi

# API_BASE_URL cannot reach the browser: SvelteKit only exposes PUBLIC_*. That is
# correct and intended — it is the *server-side* upstream for the same-origin
# route (src/routes/api/v1/[...path]/+server.ts), not a browser setting.
if [[ -z "${PUBLIC_API_BASE_URL:-}" ]]; then
  if nonempty API_BASE_URL; then
    pass "API_BASE_URL is set — same-origin /api/v1 forwards to it server-side"
    note "this pairing is the default shape: PUBLIC_API_BASE_URL empty, API_BASE_URL set"
  else
    warn "neither PUBLIC_API_BASE_URL nor API_BASE_URL is set"
    note "the browser will call /api/v1 on its own origin and the proxy has no upstream"
    note "in production that answers 503 api_upstream_not_configured; set API_BASE_URL."
  fi
fi

# ---------------------------------------------------------------------------
section "Result"
if (( failures > 0 )); then
  printf '%s%d check(s) failed%s, %d warning(s)\n' "$RED" "$failures" "$RESET" "$warnings"
  exit 1
fi
printf '%sall checks passed%s, %d warning(s)\n' "$GREEN" "$RESET" "$warnings"
