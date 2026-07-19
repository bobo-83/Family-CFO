# ADR 0035: TestFlight release — API-key upload, but the FIRST one needs Xcode's GUI

## Status

Accepted.

## Context

Household members shouldn't need a cable and a dev build to run the app. TestFlight
gives them cable-free installs and automatic updates. `scripts/release-testflight.sh`
archives a Release build and uploads it to App Store Connect, authenticating with an
App Store Connect API key (`.p8` + Key ID + Issuer ID) kept only in the gitignored
`.deploy.env` (per the credential-handling rules — the `.p8` is referenced by path,
never committed).

Two facts surfaced doing the first real upload that will otherwise waste an hour
the next time:

1. **The App Store Connect API key cannot do "cloud signing."** `xcodebuild
   -exportArchive` with `-allowProvisioningUpdates` and API-key auth fails with
   `Cloud signing permission error` / `No signing certificate "iOS Distribution"
   found` when no **Apple Distribution** certificate exists yet. API keys can
   authenticate the *upload* but are not permitted to *create* the distribution
   certificate + App Store provisioning profiles. Only an interactive Apple ID
   session (Xcode's GUI) can mint them.
2. **Just enrolling in the Apple Developer Program is not enough — Xcode caches the
   team's old "Personal Team" (free) classification.** After paying, Xcode's
   Organizer keeps signing/distribution flows tied to the stale free-team status
   and reports `Team "… (Personal Team)" is not enrolled`, even while the Accounts
   tab already shows "Developer Team." A lightweight "Download Manual Profiles"
   refresh does *not* clear it; removing and re-adding the Apple ID in
   **Xcode → Settings → Accounts** does.

## Decision

**The first TestFlight upload for a team is done once through Xcode's Organizer
GUI (Distribute App → App Store Connect → Upload, automatic signing) to create the
Apple Distribution certificate. Every upload after that runs headless via
`scripts/release-testflight.sh` with the API key.**

- The script still builds the archive and stamps `MARKETING_VERSION` from `VERSION`
  (ADR 0029) and `CURRENT_PROJECT_VERSION` from the UTC clock so each build number
  is strictly newer.
- Distribution to household members uses **Internal Testing** only — no App Review.
  External testing / a public link is deliberately NOT used: the app signs in to the
  household's self-hosted server, which Apple's reviewers cannot reach, so a beta
  review would stall. Internal testers install, then pair to the box as usual.
- Export compliance: the build sets `ITSAppUsesNonExemptEncryption=NO`, so there is
  no per-upload encryption questionnaire.

## Invariant

> The `.p8` App Store Connect key is never committed — it lives on disk and is
> referenced by `ASC_KEY_PATH` in the gitignored `.deploy.env`. Household
> distribution stays on Internal Testing (no App Review) because the app depends on
> a self-hosted server a reviewer can't reach.

## Rejected

- **All-CLI via the API key, including the first upload.** Impossible today — the
  key can't create the distribution certificate. Revisit only if Apple lets API
  keys manage signing certificates.
- **External TestFlight / public link for the family.** Needs a beta review that
  can't succeed against a self-hosted backend. Internal Testing avoids review
  entirely.
- **Committing the `.p8` (even encrypted) to simplify CI.** A private key in the
  tree is exactly what the credential rules forbid; path reference only.
