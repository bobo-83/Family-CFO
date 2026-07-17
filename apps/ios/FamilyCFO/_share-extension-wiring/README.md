# Share Extension — wiring (parked pending Apple Developer membership renewal)

The Share Extension (M102) — share a photo from another app into Family CFO,
which drops it in an App Group inbox for the app to attach to a transaction — is
**fully coded** but **not wired into the build**, because it needs the **App
Groups** capability, which requires an **active (paid) Apple Developer Program
membership**. The account's membership had lapsed and is being renewed.

## What already exists
- `FamilyCFOShareExtension/ShareViewController.swift`, `Info.plist`,
  `FamilyCFOShareExtension.entitlements` — the extension itself (App Group
  `group.com.familycfo.ios`).
- App side (already shipping, dormant until the extension exists):
  `FamilyCFO/System/SharedPhotoInbox.swift`,
  `FamilyCFO/Transactions/SharedInboxViewModel.swift`,
  `FamilyCFO/Transactions/SharedInboxAttachView.swift`, and the hook in
  `FamilyCFO/App/MainTabView.swift` (`checkSharedInbox()` on active/launch).
- `recentTransactions()` on `TransactionDetailAPI` for the attach picker.

## To finish once the membership is active
1. Restore the wired project + app entitlements:
   ```sh
   cd apps/ios/FamilyCFO
   cp _share-extension-wiring/project.pbxproj.wired FamilyCFO.xcodeproj/project.pbxproj
   cp _share-extension-wiring/FamilyCFO.entitlements FamilyCFO/FamilyCFO.entitlements
   ```
2. In Xcode (signed into the paid Apple ID), for **both** the `FamilyCFO` and
   `FamilyCFOShareExtension` targets → Signing & Capabilities → pick the paid
   team (the one that is NOT "(Personal Team)") and confirm the **App Groups**
   capability shows `group.com.familycfo.ios`. Build once (⌘B) so Xcode
   registers the App Group and creates the profiles.
3. From then on `./scripts/deploy-ios.sh` works normally.

If you need to go back to the deployable baseline, the original project file is at
`project.pbxproj.bak` in the session scratchpad (or just remove the extension
target + `CODE_SIGN_ENTITLEMENTS` from the app target).

Note: this `_share-extension-wiring/` folder is intentionally a sibling of the
target folders so it is NOT part of any Xcode synchronized group.
