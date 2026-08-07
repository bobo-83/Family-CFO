import Foundation
import Observation

/// Backs the Settings "Reserve committed savings" toggle (#5). Household-wide,
/// not per-member: reserving is a server-side computation choice (whether a
/// committed contribution is subtracted inside safe_to_spend), so this only
/// PATCHes the shared setting and lets the next context refresh carry the
/// recomputed figure everywhere else.
@MainActor
@Observable
final class CommittedSavingsReserveViewModel {
    private let api: HouseholdAPI

    private(set) var reserved = false
    var errorMessage: String?

    init(api: HouseholdAPI) {
        self.api = api
    }

    /// Best-effort read of the current value from the live Safe-to-spend, the
    /// only place the server reports it. A failed fetch keeps the default
    /// (false) rather than blocking the whole Settings screen. Absent committed
    /// savings in the horizon reports nothing, which reads as the default.
    func load() async {
        guard let context = try? await api.context(month: nil) else { return }
        reserved = context.safeToSpend?.committedSavingsReserved ?? false
    }

    /// Flip the setting, then refresh so the toggle reflects the server's
    /// recomputed state (a reserved figure now sits inside safe_to_spend).
    func setReserved(_ value: Bool) async {
        guard value != reserved else { return }
        let previous = reserved
        reserved = value  // the Toggle tracks us, so show the choice immediately
        do {
            try await api.updateReserveCommittedSavings(value)
            errorMessage = nil
            await load()
        } catch {
            reserved = previous
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
