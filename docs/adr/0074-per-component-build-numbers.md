# ADR 0074: A shared contract, independent per-component builds

## Status

Accepted. Amends ADR 0029 (one monorepo version) by separating the two questions
its single version string was answering at once.

## Context

ADR 0029 made `/VERSION` the single source of truth: every deployable is stamped
from it, and every seam — the iOS Overview banner, the OTA install page,
`patch.sh` after a box deploy, `doctor.sh`, the dashboard footer — compares it
for exact string equality. That gave a real guarantee, and it ended a class of
bug where the phone quietly ran old code against a new backend.

But one string was carrying two different questions:

1. *Were these built from the same commit?* — what equality actually tests.
2. *Can this app talk to this backend?* — what anyone looking at the banner
   wants to know.

Only the first is modelled, so the second is approximated by it. The
approximation fails in the common case: patch the box with a backend-only fix
and a byte-identical iOS app is now "stale" — orange banner on the Overview,
"versions differ" on the OTA page, `the published app is STALE` in the deploy
terminal. Nothing is wrong. Three separate warnings say something is.

ADR 0029 saw this coming. It rejected auto-stamping `/VERSION` per deploy for
exactly this reason ("deploying the API alone would instantly 'stale' a
byte-identical app") and avoided the problem by keeping bumps rare and manual
rather than by modelling compatibility. That holds only while releases are rare.

The coupling also runs the other way. `release-testflight.sh` refuses to upload a
version TestFlight has already seen — testers can only tell builds apart by the
marketing version, and five `0.119.0` builds landed in two days before it did.
So shipping anything to testers requires a fresh `/VERSION`, which means an
iOS-only release consumes a number the backend never wanted, and a backend-only
release consumes one the phone never wanted. After ~156 bumps the number records
how often we shipped *anything*, not what is compatible with what.

## Decisions

1. **`/VERSION` holds the contract, not a full version.** It is `MAJOR.MINOR`
   (e.g. `0.157`) and it is shared by every component.

2. **Each component owns a build number**, a plain integer in a `BUILD` file
   beside its source: `apps/api/BUILD`, `apps/web/BUILD`, `apps/ios/BUILD`. A
   component's full version is `<contract>.<build>` — the api reports `0.157.4`,
   the dashboard `0.157.2`, the phone `0.157.1`.

3. **Compatible iff the contract matches.** `0.157.4` and `0.157.1` are the same
   contract and must not warn. This is the invariant every seam now checks, in
   place of string equality.

4. **There are three components, and the boundary is "can a user install this
   independently".**
   - **api** — the api and worker containers plus the `services/*` packages
     baked into them. They share one Dockerfile and are one artifact.
   - **web** — the dashboard. It ships as its own image and `patch.sh web` can
     deploy it alone.
   - **ios** — the iPhone app, with the Watch app embedded in it.

   `services/*` get no version of their own. They are copied into the api image
   and cannot be deployed separately, so a number for them would describe
   nothing.

5. **The contract bumps only for a client-breaking change** — an endpoint or
   field removed or renamed in `shared/openapi/family-cfo.v1.yaml`, a type
   changed, a newly required request field, or a migration an older client
   cannot tolerate. Adding an endpoint does *not* bump it: an old client that
   does not know about a new route is not broken by it. When the contract bumps,
   every component must ship, and a one-sided deploy warns exactly as loudly as
   it does today.

6. **Every `BUILD` resets to `0` when the contract bumps.** Ordering stays
   monotonic because the contract fields dominate the comparison —
   `(0,156,9) < (0,157,0)` — so the backup restore gate
   (`_check_restore_compatibility` in `apps/api/src/family_cfo_api/backup_processing.py`)
   keeps working unchanged.

## Invariant

> Every deployable reports `<contract>.<build>`, and any two components whose
> contracts differ are surfaced automatically — in the app, on the OTA page, in
> the dashboard footer, and in the deploy terminal. Components whose contracts
> agree are compatible regardless of their build numbers, and are never warned
> about.

## Rejected

- **Keeping exact equality and bumping less often** — the status quo. It makes
  the warning correct only when releases are rare, and the warning is worth
  having precisely when they are not.
- **Auto-stamping the build from a timestamp or commit SHA.** ADR 0029 rejected
  this and the reason it gave has dissolved (a byte-identical app is no longer
  staled by a backend deploy), but a hand-bumped integer is still what
  TestFlight needs to show testers a number that means something, and a
  build nobody chose is a build nobody can talk about.
- **A version manifest file (JSON/TOML) instead of plain files.** `/VERSION` is
  read by a `tr -d '[:space:]'` one-liner in seven shell scripts, by a `COPY` in
  a Dockerfile, and by CI. A manifest drags a JSON parser into all of them and
  buys nothing that four one-line files do not.
- **Giving the dashboard the api's build number.** Tempting — the browser always
  fetches the current bundle, so there is no stale installed artifact — but
  `patch.sh web` really can deploy it alone, and a dashboard-only fix should not
  have to burn an api version to ship.
- **Server-side minimum-client enforcement** (a `426`, an `X-Client-Version`
  header). This ADR models compatibility; enforcing it is a separate decision.
  ADR 0029 declined it deliberately — "a family finance app that bricks itself
  over a minor version skew is worse than one that warns" — and that still
  stands.
