import Foundation
import Observation

/// Drives the Change password screen (#97).
///
/// The member stays signed in on this phone afterwards — the server keeps the
/// calling session and revokes the rest — so there is nothing to re-pair and
/// nothing to rewrite in the Keychain. The screen says so, because "your other
/// devices were signed out" is the consequence people need to hear.
@MainActor
@Observable
final class ChangePasswordViewModel {
    /// Same floor as the invite flow and the server's schema — one bar for
    /// setting a password, not a third one invented on the phone.
    static let minimumLength = 8

    let api: ChangePasswordAPI

    var currentPassword = ""
    var newPassword = ""
    var confirmPassword = ""

    private(set) var isSubmitting = false
    private(set) var didChange = false
    var errorMessage: String?

    init(api: ChangePasswordAPI) { self.api = api }

    var newPasswordTooShort: Bool {
        !newPassword.isEmpty && newPassword.count < Self.minimumLength
    }

    var confirmationMismatched: Bool {
        !confirmPassword.isEmpty && confirmPassword != newPassword
    }

    var canSubmit: Bool {
        !isSubmitting
            && !currentPassword.isEmpty
            && newPassword.count >= Self.minimumLength
            && confirmPassword == newPassword
    }

    func submit() async {
        guard canSubmit else { return }
        isSubmitting = true
        didChange = false
        defer { isSubmitting = false }
        do {
            try await api.change(current: currentPassword, new: newPassword)
            // The typed passwords live only in this state, and not past the
            // moment they stop being needed.
            currentPassword = ""
            newPassword = ""
            confirmPassword = ""
            errorMessage = nil
            didChange = true
        } catch let failure as ChangePasswordFailure {
            errorMessage = Self.describe(failure)
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    static func describe(_ failure: ChangePasswordFailure) -> String {
        switch failure {
        case .wrongCurrentPassword:
            return String(localized: "That is not your current password.")
        case .sameAsCurrent:
            return String(localized: "Your new password must be different from the current one.")
        case .keyCouldNotBeReminted:
            // ADR 0072: nothing was changed. Signing in again re-opens the
            // household's key, which is what the retry needs.
            return String(
                localized:
                    "Your password was not changed — this household's encryption key could not be re-created. Sign in again, then retry."
            )
        case .rateLimited(let retryAfter):
            return RetryAfter.tooManyAttemptsMessage(headerValue: retryAfter)
        }
    }
}
