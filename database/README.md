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

M1 includes an empty baseline migration only. Product tables start in later milestones after their specs are accepted.
