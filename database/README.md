# Database

Planned primary database: PostgreSQL.

This directory will contain schema definitions, migrations, seeds, and local development fixtures.

Only synthetic data belongs in this repository.

## Migrations

Alembic migration scripts live in `database/migrations`.

The API app owns the migration runner configuration for now:

```bash
cd apps/api
make migrate
```

M1 includes an empty baseline migration only. M2 adds the first product tables: `households`, `users`, `household_memberships`, `auth_sessions`, `accounts`, `account_balances`, `transactions`, `transaction_categories`, `bills`, `income_sources`, `goals`, `scenarios`, and `financial_calculations`.

Later migrations add:

- M3: `recommendations`
- M4: `recommendations.model_version`, `recommendations.prompt_version`, and `ai_runtime_configs`
- M6 backend support: `pairing_sessions`, `paired_devices`, and nullable `auth_sessions.device_id` for device-backed session revocation

## Money Storage

Every money column is a signed integer `*_minor` column (e.g. `amount_minor`, `balance_minor`, `target_minor`) paired with a 3-character `currency` column. No financial amount is ever stored as a floating-point or numeric/decimal column — see `docs/specs/03-domain-model.md` for the full money rules and `services/financial-engine` for the `Money` value type application code uses to manipulate these amounts.
