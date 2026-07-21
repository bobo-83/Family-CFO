#!/usr/bin/env bash
#
# Family CFO — seed the showcase demo dataset (M74).
#
# Persona: a senior software engineer at Anthropic living in Austin, TX, with
# TWO FULL YEARS of history — biweekly Anthropic payroll (raise a year in), an
# Austin mortgage with escrowed Travis County property taxes, seasonal Austin
# Energy bills, H-E-B groceries, matched savings transfers, bill suggestions
# and drift, budgets in all three states, goals, memories, a compensation
# profile with private-equity vests + W2 actuals, TX tax settings (no state
# income tax), and monthly net-worth history.
#
# Idempotent: re-running is a no-op once the showcase data exists.
#
# Usage: scripts/seed-demo.sh [--reset]
#   --reset   wipe the demo household's DATA first (identity — logins, roles,
#             devices — is kept), then reseed from scratch.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

RESET="${1:-}"

docker compose exec -T api python -c "
from family_cfo_api import fixtures
from family_cfo_api.config import get_settings
from family_cfo_api.db import create_database_engine
engine = create_database_engine(get_settings().database_url)
if '${RESET}' == '--reset':
    deleted = fixtures.reset_demo_data(engine)
    print(f'demo data reset: {deleted} rows deleted')
seeded = fixtures.seed_showcase_data(engine)
print('showcase data seeded' if seeded else 'showcase data already present — no-op')
"
