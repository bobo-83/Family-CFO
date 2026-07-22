import Foundation
import Observation

/// Drives the System administrators screen (ADR 0065).
@MainActor
@Observable
final class SystemAdminsViewModel {
    let api: SystemAdminsAPI

    private(set) var admins: [Components.Schemas.SystemAdmin] = []
    private(set) var isLoading = false
    private(set) var isSubmitting = false
    var errorMessage: String?

    init(api: SystemAdminsAPI) { self.api = api }

    /// The self-lockout guard, mirrored client-side so the UI can explain
    /// itself instead of offering a button that 409s.
    var canRevoke: Bool { admins.count > 1 }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            admins = try await api.list()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func grant(email: String) async {
        let trimmed = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !trimmed.isEmpty, !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            _ = try await api.grant(email: trimmed)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func revoke(userID: String) async {
        guard canRevoke, !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            try await api.revoke(userID: userID)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
