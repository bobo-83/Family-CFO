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

    private(set) var households: [Components.Schemas.HostedHousehold] = []
    private(set) var isLoading = false
    private(set) var isCreating = false
    private(set) var invite: HouseholdInvite?
    var errorMessage: String?

    init(api: HouseholdsAPI, serverBaseURL: URL) {
        self.api = api
        self.serverBaseURL = serverBaseURL
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            households = try await api.list()
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

    /// The dashboard's join link, built exactly like the Users page builds
    /// member invites: token in the FRAGMENT (not a query) so the secret never
    /// reaches server access logs.
    static func joinURL(token: String, serverBaseURL: URL) -> URL {
        URL(string: "/join#token=\(token)", relativeTo: serverBaseURL)?.absoluteURL
            ?? serverBaseURL
    }
}
