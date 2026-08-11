import Foundation
import Testing

@testable import FamilyCFO

final class MockChangePasswordAPI: ChangePasswordAPI, @unchecked Sendable {
    var calls: [(current: String, new: String)] = []
    var failure: Error?

    func change(current: String, new: String) async throws {
        calls.append((current: current, new: new))
        if let failure { throw failure }
    }
}

/// #97: the Change password screen's view model.
@MainActor
struct ChangePasswordViewModelTests {
    private func filled(_ viewModel: ChangePasswordViewModel) {
        viewModel.currentPassword = "the-current-one"
        viewModel.newPassword = "the-replacement"
        viewModel.confirmPassword = "the-replacement"
    }

    @Test func sendsBothPasswordsAndReportsSuccess() async {
        let api = MockChangePasswordAPI()
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(api.calls.count == 1)
        #expect(api.calls.first?.current == "the-current-one")
        #expect(api.calls.first?.new == "the-replacement")
        #expect(viewModel.didChange)
        #expect(viewModel.errorMessage == nil)
    }

    /// The typed passwords must not outlive the request that needed them.
    @Test func clearsTheTypedPasswordsAfterASuccessfulChange() async {
        let api = MockChangePasswordAPI()
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(viewModel.currentPassword.isEmpty)
        #expect(viewModel.newPassword.isEmpty)
        #expect(viewModel.confirmPassword.isEmpty)
    }

    @Test func refusesToSubmitATooShortNewPassword() async {
        let api = MockChangePasswordAPI()
        let viewModel = ChangePasswordViewModel(api: api)
        viewModel.currentPassword = "the-current-one"
        viewModel.newPassword = "short"
        viewModel.confirmPassword = "short"

        #expect(viewModel.newPasswordTooShort)
        #expect(!viewModel.canSubmit)
        await viewModel.submit()
        #expect(api.calls.isEmpty)
    }

    @Test func refusesToSubmitAMismatchedConfirmation() async {
        let api = MockChangePasswordAPI()
        let viewModel = ChangePasswordViewModel(api: api)
        viewModel.currentPassword = "the-current-one"
        viewModel.newPassword = "the-replacement"
        viewModel.confirmPassword = "the-replacemant"

        #expect(viewModel.confirmationMismatched)
        #expect(!viewModel.canSubmit)
        await viewModel.submit()
        #expect(api.calls.isEmpty)
    }

    @Test func refusesToSubmitWithoutTheCurrentPassword() async {
        let api = MockChangePasswordAPI()
        let viewModel = ChangePasswordViewModel(api: api)
        viewModel.newPassword = "the-replacement"
        viewModel.confirmPassword = "the-replacement"

        #expect(!viewModel.canSubmit)
        await viewModel.submit()
        #expect(api.calls.isEmpty)
    }

    @Test func wordsAWrongCurrentPasswordAsSuch() async {
        let api = MockChangePasswordAPI()
        api.failure = ChangePasswordFailure.wrongCurrentPassword
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(!viewModel.didChange)
        #expect(viewModel.errorMessage == "That is not your current password.")
    }

    /// ADR 0072: nothing changed, and the way out is another sign-in.
    @Test func explainsThatNothingChangedWhenTheKeyCouldNotBeReminted() async {
        let api = MockChangePasswordAPI()
        api.failure = ChangePasswordFailure.keyCouldNotBeReminted
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(!viewModel.didChange)
        #expect(viewModel.errorMessage?.contains("was not changed") == true)
        #expect(viewModel.errorMessage?.contains("Sign in again") == true)
    }

    /// #92: name the wait the server sent; never invent one.
    @Test func namesTheLockoutWaitFromTheHeader() async {
        let api = MockChangePasswordAPI()
        api.failure = ChangePasswordFailure.rateLimited(retryAfter: "900")
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(viewModel.errorMessage == "Too many attempts — try again in 15 minutes.")
    }

    @Test func saysLaterWhenTheServerSentNoWait() async {
        let api = MockChangePasswordAPI()
        api.failure = ChangePasswordFailure.rateLimited(retryAfter: nil)
        let viewModel = ChangePasswordViewModel(api: api)
        filled(viewModel)

        await viewModel.submit()

        #expect(viewModel.errorMessage == "Too many attempts — try again later.")
    }
}
