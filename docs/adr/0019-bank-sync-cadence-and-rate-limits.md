# ADR 0019: Bank-sync cadence & provider rate limits (M107)

## Status

Accepted. Extends ADR 0015 (institution connections via SimpleFIN).

## Context

SimpleFIN — and financial data aggregators generally — refresh account data
**about once per day** and **rate-limit a token to ~24 requests/day**. Syncing
more often gains no new data and, past the cap, the provider emails a warning and
can disconnect the app.

Two code paths hit the provider, and both were unbounded:

1. **Scheduled poller** (`worker_main.sync-bank-connections`). It was configured
   with `interval_seconds=BACKUP_INTERVAL_SECONDS` — **300 s (every 5 minutes)** —
   despite a comment claiming "daily." Unlike the backup job, it had **no cadence
   gate**, so it called the provider for every connection every 5 minutes ≈ **288
   requests/day per connection**. This tripped SimpleFIN's rate-limit warning.
2. **User pull-to-refresh** (`POST /connections/sync`, M103 made it fire from every
   tab). Each pull hit the provider for every connection with no throttle.

The root failure was a **fixed sub-daily poll with no per-connection cadence
gate**, and no test asserting the provider is not re-hit when data is already
fresh.

## Decision

Split by who initiated the sync — automatic vs. a human asking:

1. **The automatic poller is capped at once a day.** `banksync.sync_due_connections`
   syncs a connection only when `due_for_sync(connection)` is true — i.e. its
   `last_synced_at` is older than `SCHEDULED_SYNC_INTERVAL` (24 h). The poller job
   polls **hourly** (`BANK_SYNC_POLL_INTERVAL_SECONDS = 3600`, not every 5 min) and
   the gate makes the actual provider call happen ≈ once/day per connection. The
   gate keys off `last_synced_at`, so it is **restart-safe** (a worker redeploy
   doesn't re-sync a connection that synced earlier today).
2. **User pull-to-refresh always hits the provider.** `POST /connections/sync` is an
   explicit "fetch now" — it is **not** gated. A human pulls at human pace, and if
   they pull they want the freshest data the provider can give.
3. **`banksync.sync_connection` stays unconditional** (low-level "do the sync"). The
   cadence is *policy* in `sync_due_connections` / the poller, so `sync_connection`
   stays directly unit-testable and pull-to-refresh can call it freely.

## Invariant (prevents recurrence)

> **No AUTOMATIC/background code path may call a data provider on a fixed sub-daily
> schedule. Any scheduled sync MUST gate on a per-connection cadence
> (`due_for_sync`, ≥ ~1/day) so `N_connections × syncs/day` stays under the
> provider's cap. User-initiated syncs are exempt — they are human-paced.**

## Guardrail tests

- `test_banksync.test_due_for_sync_is_a_daily_gate` — a connection synced 5 minutes
  (or 12 hours) ago is **not** due; the interval stays ≥ 20 h.
- `test_banksync.test_sync_due_connections_syncs_at_most_once_a_day` — the poller
  syncs a connection once, then skips it on the next (hourly) run.
- `test_connections_api.test_pull_to_refresh_always_syncs` — a manual pull hits the
  provider every time, even right after linking.

## Consequences

- Automatic freshness is ~once/day, matching the provider. New charges appear
  within a day on their own; a user who wants them sooner pulls to refresh.
- Pull-to-refresh is unbounded by design. With SimpleFIN's ~24/day cap this is fine
  for human use (1 connection here); if a household ever pulls enough to approach
  the cap, revisit (e.g. a soft client-side minimum between pulls).
