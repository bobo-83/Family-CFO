# ADR 0029: One monorepo version, verified at every seam (M120)

## Status

Accepted.

## Context

The API reported a hand-maintained package version ("0.2.0"), the iOS app had a
manually bumped build number, the OTA page showed only a build timestamp, and
nothing compared any of them. The phone could quietly run old code against a
new backend — the user: "Make sure the OTA is always on the most current code…
we should have a version number where both the backend and the iOS app matches.
Since this is a monorepo everything ships with the same version."

## Decisions

1. **`/VERSION` at the repo root is the single source of truth** (currently
   `0.119.0`; bump it with each meaningful release). Everything ships stamped
   from it:
   - **API/worker**: baked into the image (`/app/VERSION`); `Settings.from_env`
     reads it (env override still wins) → `GET /health` reports it.
   - **iOS**: both deploy scripts pass `MARKETING_VERSION=$(cat VERSION)` to
     xcodebuild, plus `CURRENT_PROJECT_VERSION=$(date -u +%Y%m%d%H%M)` — a
     monotonic build number, so over-the-top installs never fight a stale
     manual counter again.
   - **Web**: served from the same rsynced tree by the same box; the shell
     footer shows the running version (one `/health` fetch).
   - **OTA**: the page shows its build's version, and a published
     `ota/VERSION` marker records it machine-readably.
2. **Every seam verifies, none assumes.**
   - The **OTA page self-checks**: it fetches `/api/v1/health` (same origin)
     and shows "✓ Matches the box (v…)" or a loud "versions differ" warning
     naming both numbers — so the moment you open the install page you know
     whether this build belongs to this backend.
   - The **iOS app checks at load**: the Overview compares its embedded version
     against `/health` and shows an orange banner ("App v… · box v…") with a
     link to the OTA page when they differ. The More tab shows the app version.
   - **`patch.sh` checks after every box deploy**: it reads the box's
     `ota/VERSION` and warns "the published app is STALE — run
     deploy-ios-ota.sh" when the backend has moved past the bundle. This is the
     "OTA is always on the most current code" enforcement: drift is caught the
     moment it is created, at the terminal that created it.
3. **Matching, not freshness, is the invariant.** A version match means the app
   and backend were built from the same tree — the actual guarantee that
   matters. The health check is best-effort everywhere (a failed fetch shows
   nothing rather than breaking a screen).

## Invariant

> Every deployable reports the same `/VERSION` it was built from. Any
> app↔backend mismatch is surfaced automatically — on the OTA page, in the
> app, and in the deploy terminal — never discovered by debugging.

## Rejected

- **Auto-stamping VERSION per deploy (timestamp/content hash)** — deploying the
  API alone would instantly "stale" a byte-identical app; a hand-bumped release
  version keeps the signal meaningful. (The repo is not a git checkout on the
  box, so commit SHAs weren't available anyway.)
- **Blocking deploys on mismatch** — the box must be patchable while the phone
  is away (OTA exists for exactly that); warn loudly instead.
- **Forcing the app to refuse to run on mismatch** — a family finance app that
  bricks itself over a minor version skew is worse than one that warns.
