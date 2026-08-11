import Foundation

/// #96 (ADR 0072 Phase 3): sealed mode is the strongest protection this app
/// offers and a household only ever found it by scrolling the Backups screen.
/// This view model decides whether to OFFER it on Settings → Household — it
/// never switches anything on. Sealed stays off by default; it cannot even be
/// on at creation, since sealing needs a member wrap and a recovery key and
/// neither exists yet (`seal_household`).
///
/// Dismissal is per DEVICE (UserDefaults), deliberately, not per household:
/// the offer explains a household decision to a *person*, and one member
/// tapping Dismiss on their phone must not silently hide the feature from the
/// co-owner who would actually turn it on. It is also the posture the advisor
/// disclaimer already uses ("family-cfo.showAdvisorDisclaimer"), and it needs
/// no API contract change.
@MainActor
@Observable
final class SealedModeOfferViewModel {
    nonisolated static let dismissedKey = "family-cfo.sealedModeOfferDismissed"

    private let api: BackupAPI
    private let defaults: UserDefaults

    private(set) var status: Components.Schemas.HouseholdKeyStatus?
    private(set) var isDismissed: Bool

    init(api: BackupAPI, defaults: UserDefaults = .standard) {
        self.api = api
        self.defaults = defaults
        self.isDismissed = defaults.bool(forKey: Self.dismissedKey)
    }

    /// Best-effort: a box that cannot answer simply makes no offer.
    func load() async {
        guard !isDismissed else { return }
        status = try? await api.householdKeyStatus()
    }

    func dismiss() {
        defaults.set(true, forKey: Self.dismissedKey)
        isDismissed = true
    }

    /// Offer sealing only where it is actually possible: per-household
    /// encryption enabled on the box, and the household not already sealed.
    var isOffered: Bool {
        guard !isDismissed, let status else { return false }
        return status.encryptionEnabled && status.mode != .sealed
    }

    /// A missing precondition does NOT hide the offer — it names what to make
    /// first. "Create a recovery key" is actionable; a greyed-out row is not.
    var needsRecoveryKey: Bool { status.map { !$0.hasRecoveryKey } ?? false }
    var needsMemberKey: Bool { (status?.memberWraps ?? 0) < 1 }
}
