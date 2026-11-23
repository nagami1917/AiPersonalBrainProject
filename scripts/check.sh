#!/usr/bin/env bash
# One command that mirrors CI: lint, typecheck, unit tests, and (when a database
# is available) the schema/RLS/retrieval suite.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> backend: ruff"
(cd apps/api && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests)

echo "==> backend: mypy"
(cd apps/api && .venv/bin/mypy app)

echo "==> backend: unit tests"
(cd apps/api && .venv/bin/pytest -q tests/unit)

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "==> backend: database tests"
  (cd apps/api && .venv/bin/pytest -q tests/db)
else
  echo "==> backend: database tests skipped (DATABASE_URL unset)"
fi

echo "==> frontend: unit tests"
(cd apps/web && npm run test --silent)

echo "==> frontend: svelte-check"
(cd apps/web && npm run check)

echo "==> frontend: build"
(cd apps/web && npm run build)

echo "==> deployment: environment configuration"
# Exercises the checker against a known-good set, so a broken check fails here
# rather than on someone's deploy. Values are blank: nothing real is involved.
( env -i PATH="$PATH" \
    SUPABASE_URL=https://example.supabase.co SUPABASE_ANON_KEY=x \
    SUPABASE_SERVICE_ROLE_KEY=x SUPABASE_JWT_SECRET=$(printf 'x%.0s' {1..40}) \
    DATABASE_URL=postgresql://u:p@h/db OPENAI_API_KEY=x \
    CORS_ALLOWED_ORIGINS=https://app.example \
    PUBLIC_SUPABASE_URL=https://example.supabase.co PUBLIC_SUPABASE_ANON_KEY=x \
    ./scripts/check-env.sh >/dev/null ) \
  && echo "check-env.sh accepts a valid configuration" \
  || { echo "check-env.sh rejected a valid configuration"; exit 1; }

echo "all checks passed"
