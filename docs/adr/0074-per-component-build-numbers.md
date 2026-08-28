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

3. **A contract match is the compatibility signal.** A component may keep the
   same contract only while it remains compatible with every released
   counterpart carrying that contract. `0.157.4` and `0.157.1` therefore must
   not warn: the release policy below guarantees that they can work together.
   "Compatible" means every shipped and enabled client feature works; a feature
   that negotiates capabilities and stays unavailable when an endpoint is absent
   does not require that capability.

4. **There are three versioned product components.** The boundary is an
   independently released end-user artifact whose compatibility is surfaced at
   an app↔server seam. Container isolation alone does not create a component.
   - **api** — the api and worker containers plus the five in-process service
     packages installed into their shared image. They are one released artifact.
   - **web** — the dashboard. It ships as its own image and `patch.sh web` can
     deploy it alone.
   - **ios** — the iPhone app, with the Watch app embedded in it.

   `services/model-manager` is the deliberate exception: it is an optional,
   separately built operational sidecar, not a published end-user artifact, and
   does not participate in the app↔server contract. Its internal HTTP interface
   with the api must remain bidirectionally compatible across rolling deploys
   and rollback: current-api/previous-manager and previous-api/current-manager
   are both integration-test cases. An incompatible change requires a follow-up
   ADR that first promotes model-manager to a versioned component and defines an
   ordered rollout; shipping both in one non-atomic Compose patch is not enough.
   Third-party/runtime infrastructure such as vLLM, Qdrant, SearXNG, and
   monitoring is outside this product-version scheme.

5. **The contract bumps whenever any released client/server pairing would break,
   in either direction.** That includes an endpoint or field removed or renamed
   in `shared/openapi/family-cfo.v1.yaml`, a type changed, a newly required
   request field, a migration an older client cannot tolerate, **or a client
   beginning to require an additive capability an older server does not have**.
   Adding an endpoint alone does not bump the contract while no released client
   requires it. Before the first dependent client ships, bump the contract and
   deploy the api side first; a client must never ship under contract `C` if it
   requires a capability absent from **any released** api carrying `C`. Release
   checks must exercise a new client against the oldest published api artifact
   (or an immutable compatibility fixture) for its contract so that dependency
   cannot merge unnoticed. When the contract bumps, every component must ship,
   and a one-sided deploy warns exactly as loudly as it does today.

6. **Every `BUILD` resets to `0` when the contract bumps.** Ordering stays
   monotonic because the contract fields dominate the comparison —
   `(0,156,9) < (0,157,0)` — so the backup restore gate
   (`_check_restore_compatibility` in `apps/api/src/family_cfo_api/backup_processing.py`)
   keeps working unchanged.

## Enforcement

The invariant below is only worth as much as the checks behind it. Both are
specified here rather than left to the implementation, because a rule that
depends on reviewers noticing is not a guarantee. Neither can be written on this
branch — both must resolve a contract, and `/VERSION`, the `BUILD` files and
`scripts/lib/version.sh` all arrive with the version scheme itself — so they are
binding requirements on that work.

1. **A client may not merge requiring a capability the oldest api on its
   contract lacks.** The PR-time gate that validates `/VERSION` resolves the
   client's contract `C`, resolves the oldest published api artifact carrying
   `C` (or a pinned fixture standing in for it), and fails when the client
   exercises a route or field that artifact does not serve. This is the
   enforcement point for Decision 5. Without it the additive-endpoint skew this
   ADR exists to close stays reachable: `patch.sh` supports the one-sided deploy
   that produces it, and every seam reports compatible while it does.

2. **The api↔model-manager interface is covered in both directions.**
   `current-api`/`previous-manager` and `previous-api`/`current-manager` are both
   cases. Decision 4 puts model-manager outside the version scheme, so no seam
   will ever warn on a mismatch there and these tests are the only protection
   that interface has. A change that cannot satisfy both directions is precisely
   the trigger for the promoting ADR Decision 4 requires.

Neither check exists today. The version scheme is not complete until both do,
and a green build before then means the decisions above were followed, not that
they were verified.

## Invariant

> Every versioned product component reports `<contract>.<build>`. A contract
> match guarantees compatibility because no component may ship under a contract
> while requiring a capability absent from any released counterpart carrying that
> contract. A contract mismatch is surfaced automatically — in the app, on the
> OTA page, in the dashboard footer, and in the deploy terminal — regardless of
> build numbers. Operational sidecars outside this scheme remain bidirectionally
> compatible across rolling deploys and rollback.

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
