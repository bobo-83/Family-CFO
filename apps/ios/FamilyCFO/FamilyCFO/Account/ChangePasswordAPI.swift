import Foundation

/// #97: retiring your own password.
///
/// The current password is sent alongside the new one on purpose — holding a
/// session is not evidence the member is at the phone. On success the server
/// revokes every OTHER session for this member and keeps the one this app is
/// using, so the stored credential in the Keychain stays valid and there is
/// nothing to re-pair.
protocol ChangePasswordAPI: Sendable {
    func change(current: String, new: String) async throws
}

/// Distinguishes the refusals the screen must word differently from a generic
/// server error. `rateLimited` carries the server's wait so the UI can name it
/// rather than invent one (#92).
enum ChangePasswordFailure: Error, Equatable {
    case wrongCurrentPassword
    case sameAsCurrent
    case keyCouldNotBeReminted
    case rateLimited(retryAfter: String?)
}

struct LiveChangePasswordAPI: ChangePasswordAPI {
    let client: Client

    func change(current: String, new: String) async throws {
        let body = Components.Schemas.PasswordChangeRequest(
            currentPassword: current,
            newPassword: new
        )
        switch try await client.changePassword(.init(body: .json(body))) {
        case .noContent:
            return
        case .badRequest:
            throw ChangePasswordFailure.sameAsCurrent
        case .unauthorized:
            // The SESSION is dead, which is a different problem from a wrong
            // password — the server answers 403 for that.
            throw APIError.unauthorized
        case .forbidden:
            throw ChangePasswordFailure.wrongCurrentPassword
        case .conflict:
            // ADR 0072: the member's key wrap could not be re-minted, so the
            // server changed nothing rather than leave them able to sign in and
            // unable to decrypt.
            throw ChangePasswordFailure.keyCouldNotBeReminted
        case .tooManyRequests(let response):
            throw ChangePasswordFailure.rateLimited(retryAfter: response.headers.retryAfter)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
