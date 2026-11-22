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

echo "==> frontend: svelte-check"
(cd apps/web && npm run check)

echo "==> frontend: build"
(cd apps/web && npm run build)

echo "all checks passed"
