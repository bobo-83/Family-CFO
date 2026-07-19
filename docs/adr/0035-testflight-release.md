# ADR 0035: TestFlight release — fully automated with an Admin API key

## Status

Accepted. (Supersedes the original "first upload needs Xcode's GUI" conclusion —
that was a symptom of a non-Admin key, see below.)

## Context

Household members shouldn't need a cable and a dev build to run the app.
TestFlight gives them cable-free installs and automatic updates.
`scripts/release-testflight.sh` archives a Release build and ships it to App
Store Connect, authenticating with an App Store Connect API key (`.p8` + Key ID
+ Issuer ID) kept only in the gitignored `.deploy.env` — the `.p8` is referenced
by path, never committed.

Getting the automation working surfaced three facts that will otherwise waste
hours next time:

1. **Cloud signing via the API key works only if the key has ADMIN access.**
   `xcodebuild -exportArchive` with `-allowProvisioningUpdates` + API-key auth
   creates the distribution certificate + App Store profile on the fly ("cloud
   signing"). With a **Developer/App Manager** key it fails with `Cloud signing
   permission error` / `No signing certificate "iOS Distribution" found`. With
   an **Admin** key it succeeds and needs no pre-existing cert. (You can't change
   a key's role — generate a new Admin key.)
2. **`ASC_KEY_ID` must match the `.p8`.** Apple names the file
   `AuthKey_<KEYID>.p8`. If `ASC_KEY_ID` and the file's key id disagree, the JWT
   is signed with the wrong key and App Store Connect returns **401**
   (`Communication with Apple failed` / `No Accounts with App Store Connect
   Access`). This masquerades as a signing/account problem but is pure config.
3. **Just enrolling in the Developer Program isn't enough at first** — Xcode
   caches the team's old free "Personal Team" status and reports `Team "…
   (Personal Team)" is not enrolled` until you remove and re-add the Apple ID in
   Xcode → Settings → Accounts.

## Decision

**Releases are fully automated — no Xcode GUI. `scripts/release-testflight.sh`
archives, exports a *signed* `.ipa` (`destination: export`, cloud-signed by the
Admin API key), then uploads it with `xcrun altool --upload-app` using the same
key.** Splitting export from upload is more reliable than the combined
`destination: upload`, which was finicky with key-only auth.

- The script stamps `MARKETING_VERSION` from `VERSION` (ADR 0029) and
  `CURRENT_PROJECT_VERSION` from the UTC clock so each build number is strictly
  newer, and sets `ITSAppUsesNonExemptEncryption=NO` (no per-upload encryption
  questionnaire).
- `altool` locates the key by id in a `private_keys` search dir; the script
  copies the `.p8` to `~/.appstoreconnect/private_keys/AuthKey_<KEYID>.p8` — on
  disk, never committed.
- Distribution to household members uses **Internal Testing** only (no App
  Review). External testing / a public link is avoided on purpose: the app signs
  in to the household's self-hosted server, which Apple's reviewers can't reach,
  so a beta review would stall.

## Invariant

> The `.p8` is never committed — it lives on disk, referenced by `ASC_KEY_PATH`
> in the gitignored `.deploy.env`, and `ASC_KEY_ID` always matches that file's
> key id. The upload key has Admin access. Household distribution stays on
> Internal Testing (no App Review) because the app depends on a self-hosted
> server a reviewer can't reach.

## Rejected

- **Requiring Xcode's Organizer GUI for the first upload.** The original
  conclusion — it turned out the GUI only "worked" because it uses the
  interactive Apple ID (which has Admin rights). An Admin API key does the same
  thing headlessly; the GUI is unnecessary.
- **Combined `xcodebuild -exportArchive destination: upload`.** Flaky with
  key-only auth (`No Accounts with App Store Connect Access`); export-then-altool
  is explicit and reliable.
- **External TestFlight / public link for the family.** Needs a beta review that
  can't succeed against a self-hosted backend.
- **Committing the `.p8` (even encrypted).** A private key in the tree is exactly
  what the credential rules forbid; path reference only.
