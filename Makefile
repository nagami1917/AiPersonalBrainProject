# NEXUS — developer entry points.
# Every target here is meant to work from a clean checkout with only
# python3, node, docker (for the Supabase CLI stack) and the Supabase CLI.

.DEFAULT_GOAL := help
SHELL := /bin/bash

WEB := apps/web
API := apps/api

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------
.PHONY: install
install: install-api install-web ## install backend + frontend deps
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example — fill in secrets")

.PHONY: install-api
install-api: ## create venv and install backend deps
	cd $(API) && python3 -m venv .venv && .venv/bin/pip install -q -U pip && .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt

.PHONY: install-web
install-web: ## install frontend deps
	cd $(WEB) && npm install

# --- local stack -------------------------------------------------------------
.PHONY: db-start
db-start: ## start the local Supabase stack (requires docker + supabase CLI)
	supabase start

.PHONY: db-stop
db-stop: ## stop the local Supabase stack
	supabase stop

.PHONY: db-reset
db-reset: ## re-apply migrations + seed from scratch
	supabase db reset

.PHONY: db-new
db-new: ## create a new migration (make db-new name=add_thing)
	supabase migration new $(name)

.PHONY: db-lint
db-lint: ## lint migrations with the Supabase linter
	supabase db lint --level warning

# --- run ---------------------------------------------------------------------
.PHONY: dev
dev: ## run api + web together
	@echo "starting api on :8000 and web on :5173"
	@trap 'kill 0' INT TERM; \
	( cd $(API) && .venv/bin/uvicorn app.main:app --reload --port 8000 ) & \
	( cd $(WEB) && npm run dev -- --port 5173 --host 0.0.0.0 ) & \
	wait

.PHONY: api
api: ## run the API only
	cd $(API) && .venv/bin/uvicorn app.main:app --reload --port 8000

.PHONY: worker
worker: ## run the background worker
	cd $(API) && .venv/bin/python -m app.workers.main

.PHONY: web
web: ## run the frontend only
	cd $(WEB) && npm run dev -- --port 5173 --host 0.0.0.0

# --- quality -----------------------------------------------------------------
.PHONY: lint
lint: lint-api lint-web ## lint everything

.PHONY: lint-api
lint-api: ## ruff + mypy
	cd $(API) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests && .venv/bin/mypy app

.PHONY: lint-web
lint-web: ## svelte-check
	cd $(WEB) && npm run check

.PHONY: fmt
fmt: ## format backend
	cd $(API) && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests

.PHONY: test
test: test-api ## run all tests

.PHONY: test-api
test-api: ## pytest (needs DATABASE_URL pointing at a migrated database)
	cd $(API) && .venv/bin/pytest -q

.PHONY: test-db
test-db: ## schema + RLS + retrieval tests only
	cd $(API) && .venv/bin/pytest -q tests/db

# --- deploy ------------------------------------------------------------------
.PHONY: migrate-remote
migrate-remote: ## push migrations to the linked Supabase project
	supabase db push

.PHONY: deploy-api
deploy-api: ## deploy web + worker to Fly
	fly deploy

.PHONY: logs
logs: ## tail Fly logs
	fly logs
