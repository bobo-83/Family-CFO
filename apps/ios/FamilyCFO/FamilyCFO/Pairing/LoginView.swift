import SwiftUI

/// ADR 0056: sign in with email + password instead of scanning a QR — for a
/// member who joined via an invite link, or re-entry after an unpair. The
/// result is an ordinary paired device (visible and revocable on the
/// dashboard's Devices page).
struct LoginView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel = LoginViewModel()

    var body: some View {
        NavigationStack {
            Form {
                switch viewModel.step {
                case .enterServer, .checkingServer:
                    serverSection
                case .confirmServer(let baseURL, let fingerprint):
                    confirmSection(baseURL: baseURL, fingerprint: fingerprint)
                case .credentials(let baseURL, _):
                    credentialsSection(baseURL: baseURL)
                case .signingIn:
                    Section {
                        HStack {
                            ProgressView()
                            Text("Signing in…").padding(.leading, 8)
                        }
                    }
                case .failed(let message):
                    Section {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                        Button("Try again") { viewModel.startOver() }
                    }
                }
            }
            .navigationTitle("Sign in")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private var serverSection: some View {
        Section {
            TextField("192.168.1.10:8443", text: $viewModel.serverAddress)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button {
                Task { await viewModel.checkServer() }
            } label: {
                if case .checkingServer = viewModel.step {
                    HStack {
                        ProgressView()
                        Text("Checking…").padding(.leading, 8)
                    }
                } else {
                    Text("Continue")
                }
            }
            .disabled(viewModel.serverAddress.isEmpty)
        } header: {
            Text("Your Family CFO server")
        } footer: {
            Text("The address you use for the dashboard — ask whoever runs the box if unsure.")
        }
    }

    private func confirmSection(baseURL: URL, fingerprint: String?) -> some View {
        Section {
            LabeledContent("Server", value: baseURL.host() ?? baseURL.absoluteString)
            LabeledContent(
                "Certificate", value: LoginViewModel.shortFingerprint(fingerprint))
            Button("This is my server — continue") { viewModel.confirmServer() }
            Button("Start over", role: .cancel) { viewModel.startOver() }
        } header: {
            Text("Confirm the server")
        } footer: {
            Text(
                "The app will only ever talk to the server presenting this certificate — the same trust as scanning the pairing QR."
            )
        }
    }

    private func credentialsSection(baseURL: URL) -> some View {
        Section {
            TextField("Email", text: $viewModel.email)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            SecureField("Password", text: $viewModel.password)
            TextField("Device name", text: $viewModel.deviceName)
            if let error = viewModel.signInError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
                    .font(.callout)
            }
            Button("Sign in") {
                Task { await viewModel.signIn(into: model) }
            }
            .disabled(viewModel.email.isEmpty || viewModel.password.isEmpty)
        } header: {
            Text("Your account on \(baseURL.host() ?? "the box")")
        } footer: {
            Text("This device becomes a paired device — it appears on the dashboard's Devices page and can be revoked there.")
        }
    }
}
