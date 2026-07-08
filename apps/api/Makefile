PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTHONPATH := src
export PYTHONPATH

.PHONY: install test lint format openapi check-openapi migrate run worker

install:
	$(PYTHON) -m pip install -e ".[dev]" -e ../../services/financial-engine -e ../../services/ai-orchestrator -e ../../services/ocr-worker -e ../../services/scheduler -e ../../services/backup

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format src tests

openapi:
	$(PYTHON) -c 'from family_cfo_api.tools.openapi import generate_openapi; raise SystemExit(generate_openapi())'

check-openapi:
	$(PYTHON) -c 'from family_cfo_api.tools.openapi import check_openapi; raise SystemExit(check_openapi())'

migrate:
	$(PYTHON) -m alembic -c alembic.ini upgrade head

run:
	$(PYTHON) -m uvicorn family_cfo_api.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m family_cfo_api.worker_main
