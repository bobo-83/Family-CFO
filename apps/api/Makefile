PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTHONPATH := src
export PYTHONPATH

.PHONY: install test coverage lint format openapi check-openapi migrate run worker

# Modules held to 100% line coverage. This is not a repo-wide target and should
# not become one — chasing a global number buys tests written for the counter.
# It is the list of modules where an untested branch has already cost real data:
# household_crypto, after the August key-generation incident (migration 0091).
COVERED_MODULES ?= family_cfo_api.household_crypto

install:
	$(PYTHON) -m pip install -e ".[dev]" -e ../../services/financial-engine -e ../../services/ai-orchestrator -e ../../services/ocr-worker -e ../../services/scheduler -e ../../services/backup

test:
	$(PYTHON) -m pytest

# The full suite plus the gate — CI runs this instead of `test`, so the crypto
# module cannot lose coverage without the build going red.
coverage:
	$(PYTHON) -m pytest --cov=$(COVERED_MODULES) --cov-report=term-missing --cov-fail-under=100

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
