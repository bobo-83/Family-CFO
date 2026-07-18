# Share Extension — wiring (applied / live)

The Share Extension (M102) — share a photo from another app into Family CFO,
which drops it in an App Group inbox for the app to attach to a transaction — is
**wired into the build and live**. The extension target and the App Group
(`group.com.familycfo.ios`) are in `FamilyCFO.xcodeproj/project.pbxproj` and the
app's `FamilyCFO/FamilyCFO.entitlements`. It needs an **active (paid) Apple
Developer Program membership** for the App Groups capability.

This folder is kept as the reference copy of the wiring (a sibling of the target
folders so it is **not** part of any Xcode synchronized group).

## What's here

- `project.pbxproj.wired` — the project file with the extension target wired in
  (the applied state; kept for reference/diffing).
- `FamilyCFO.entitlements` — the app-side App Group entitlement.

## What's in the app (all shipping)

- `FamilyCFOShareExtension/` — the extension (`ShareViewController.swift`,
  `Info.plist`, `FamilyCFOShareExtension.entitlements`).
- App side: `FamilyCFO/System/SharedPhotoInbox.swift`,
  `FamilyCFO/Transactions/SharedInboxViewModel.swift`,
  `FamilyCFO/Transactions/SharedInboxAttachView.swift`, the hook in
  `FamilyCFO/App/MainTabView.swift` (`checkSharedInbox()` on active/launch), and
  `recentTransactions()` on `TransactionDetailAPI` for the attach picker.

## Signing

The project ships with an **empty `DEVELOPMENT_TEAM`** (no committed team id,
[ADR 0030](../../../../docs/adr/0030-no-personal-identifiers.md)). Builds inject
your team from **`IOS_TEAM_ID`** (see `apps/ios/README.md`); the deploy scripts
apply it to both the `FamilyCFO` and `FamilyCFOShareExtension` targets. In Xcode,
signing into your paid Apple ID and selecting your team on both targets does the
same — do **not** re-commit a team id into the project file.
