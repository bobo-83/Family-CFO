import Foundation
import Observation

/// The one-time join link for a freshly-minted household's first owner —
/// carried until the operator dismisses it (the token is stored hashed and
/// can never be shown again).
struct HouseholdInvite: Equatable {
    let householdName: String
    let ownerEmail: String
    let joinURL: URL
    let expiresAt: Date
}

/// Drives the Households screen (#180, ADR 0025 parity with the dashboard's
/// Households page): every family this box hosts, plus minting a new
/// household shell with its first-owner invite.
@MainActor
@Observable
final class HouseholdsViewModel {
    private let api: HouseholdsAPI
    private let serverBaseURL: URL
    /// The household the operator belongs to — it NEVER shows a Delete action
    /// (#189); the server's 409 is only the backstop.
    private let currentHouseholdID: String?

    private(set) var households: [Components.Schemas.HostedHousehold] = []
    var offboxRetentionDays: Int = 0
    private(set) var isLoading = false
    private(set) var isCreating = false
    private(set) var isDeleting = false
    private(set) var invite: HouseholdInvite?
    var errorMessage: String?

    init(api: HouseholdsAPI, serverBaseURL: URL, currentHouseholdID: String? = nil) {
        self.api = api
        self.serverBaseURL = serverBaseURL
        self.currentHouseholdID = currentHouseholdID
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let payload = try await api.list()
            households = payload.households
            offboxRetentionDays = payload.offboxBackupRetentionDays
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Mints the household; on success `invite` carries the one-time link and
    /// the list reloads. Returns whether it succeeded so the sheet can advance.
    func create(displayName: String, baseCurrency: String, ownerEmail: String) async -> Bool {
        guard !isCreating else { return false }
        isCreating = true
        defer { isCreating = false }
        let email = ownerEmail.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let response = try await api.create(
                displayName: displayName.trimmingCharacters(in: .whitespacesAndNewlines),
                baseCurrency: baseCurrency.trimmingCharacters(in: .whitespacesAndNewlines)
                    .uppercased(),
                ownerEmail: email)
            invite = HouseholdInvite(
                householdName: response.household.name,
                ownerEmail: email,
                joinURL: Self.joinURL(token: response.inviteToken, serverBaseURL: serverBaseURL),
                expiresAt: response.inviteExpiresAt)
            errorMessage = nil
            await load()
            return true
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return false
        }
    }

    func dismissInvite() {
        invite = nil
    }

    /// Whether a row may offer Delete — everything except the operator's own
    /// household.
    func canDelete(_ household: Components.Schemas.HostedHousehold) -> Bool {
        household.id != currentHouseholdID
    }

    /// #189: permanently removes a hosted household; the view confirmed first.
    /// On success the roster reloads; a 409/404 carries the server's human
    /// message — shown verbatim.
    func delete(_ household: Components.Schemas.HostedHousehold) async {
        guard !isDeleting else { return }
        isDeleting = true
        defer { isDeleting = false }
        do {
            try await api.delete(householdID: household.id)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The dashboard's join link, built exactly like the Users page builds
    /// member invites: token in the FRAGMENT (not a query) so the secret never
    /// reaches server access logs.
    static func joinURL(token: String, serverBaseURL: URL) -> URL {
        URL(string: "/join#token=\(token)", relativeTo: serverBaseURL)?.absoluteURL
            ?? serverBaseURL
    }
}
