#!/usr/bin/env bash
#
# Family CFO — seed the showcase demo dataset (M74).
#
# Layers a rich, feature-complete dataset onto the demo household so every
# page and advisor tool has meaningful data: account spectrum + EF designation
# + debt terms, paycheck clustering, transfer suppression, bill suggestions
# and drift, budgets in all three states, goals, memories, a compensation
# profile with W2 actuals, CA tax settings, and net-worth history.
#
# Idempotent: re-running is a no-op once the showcase data exists.
#
# Usage: scripts/seed-demo.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose exec -T api python -c "
from family_cfo_api import fixtures
from family_cfo_api.config import get_settings
from family_cfo_api.db import create_database_engine
engine = create_database_engine(get_settings().database_url)
seeded = fixtures.seed_showcase_data(engine)
print('showcase data seeded' if seeded else 'showcase data already present — no-op')
"
