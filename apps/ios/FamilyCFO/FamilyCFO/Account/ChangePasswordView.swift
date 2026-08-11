import SwiftUI

/// #97: a member retires their own password.
///
/// Pushed from Settings, so it deliberately does NOT wrap itself in a
/// `NavigationStack` — MainTabView already provides one.
struct ChangePasswordView: View {
    @State var viewModel: ChangePasswordViewModel

    var body: some View {
        Form {
            Section {
                SecureField("Current password", text: $viewModel.currentPassword)
                    .textContentType(.password)
            } header: {
                Text("Confirm it's you")
            } footer: {
                Text("Being signed in on this phone is not proof you are holding it, so your current password is needed too.")
            }

            Section {
                SecureField("New password", text: $viewModel.newPassword)
                    .textContentType(.newPassword)
                SecureField("Confirm new password", text: $viewModel.confirmPassword)
                    .textContentType(.newPassword)
                if viewModel.newPasswordTooShort {
                    Label("Password must be at least 8 characters.", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                if viewModel.confirmationMismatched {
                    Label("The two new passwords do not match.", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            } header: {
                Text("New password")
            }

            Section {
                Button {
                    Task { await viewModel.submit() }
                } label: {
                    if viewModel.isSubmitting {
                        HStack { ProgressView(); Text("Changing…").padding(.leading, 8) }
                    } else {
                        Label("Change password", systemImage: "key")
                    }
                }
                .disabled(!viewModel.canSubmit)

                if let error = viewModel.errorMessage {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                if viewModel.didChange {
                    Label("Your password was changed. You are still signed in on this phone; everywhere else signed in as you has been signed out.", systemImage: "checkmark.circle")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            } footer: {
                Text("Changing your password signs out every other browser and phone signed in as you — the usual reason to change it is that someone else knows it. This phone stays signed in.")
            }
        }
        .navigationTitle("Change password")
        .navigationBarTitleDisplayMode(.inline)
    }
}
