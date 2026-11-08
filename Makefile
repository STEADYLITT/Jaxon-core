.PHONY: setup infra migrate api worker web test test-live lint up down

VENV ?= .venv
PY := $(VENV)/bin/python

setup:            ## create venv + install backend and frontend deps
	python3.12 -m venv $(VENV)
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm ci

infra:            ## start only postgres + redis in docker
	docker compose up -d postgres redis

migrate:
	cd backend && ../$(PY) -m alembic upgrade head

api:
	cd backend && ../$(VENV)/bin/uvicorn jaxon.main:app --reload --port 8000

worker:
	cd backend && ../$(PY) -m jaxon.workers.run_worker

web:
	cd frontend && npm run dev

test:             ## unit + integration (needs infra running)
	cd backend && ../$(PY) -m pytest -m "not live"
	cd frontend && npm test && npm run typecheck

test-live:        ## real Anthropic API calls (needs ANTHROPIC_API_KEY)
	cd backend && ../$(PY) -m pytest tests/live -m live -v

lint:
	cd backend && ../$(VENV)/bin/ruff check . && ../$(VENV)/bin/ruff format --check . && ../$(VENV)/bin/mypy jaxon
	cd frontend && npm run lint

up:               ## full stack in docker
	docker compose up --build

down:
	docker compose down
