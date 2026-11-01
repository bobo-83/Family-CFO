import SwiftUI

/// The unauthenticated root: account-only device sign-in (ADR 0079).
struct LoginView: View {
    @Environment(AppModel.self) private var model
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
            .task {
                viewModel.restoreServer(model.server)
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
            .disabled(viewModel.serverAddress.isEmpty || viewModel.step == .checkingServer)
        } header: {
            Text("Your Family CFO server")
        } footer: {
            // #54: the shape belongs on screen BEFORE the attempt. The example
            // used to live only in the failure message, so the address that
            // needed correcting was the one that never saw it.
            Text(
                "The address you use for the dashboard, for example 192.168.1.10:8443 — ask whoever runs the box if unsure. Port 8443 is assumed if you leave it off."
            )
        }
    }

    private func confirmSection(baseURL: URL, fingerprint: String?) -> some View {
        Section {
            LabeledContent("Server", value: baseURL.host() ?? baseURL.absoluteString)
            LabeledContent("Certificate") {
                Text(fingerprint ?? String(localized: "System-validated certificate"))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button("This is my server — continue") { viewModel.confirmServer() }
            Button("Start over", role: .cancel) { viewModel.startOver() }
        } header: {
            Text("Confirm the server")
        } footer: {
            Text(
                "Compare the full fingerprint with the operator through a trusted channel before continuing. CA-signed servers use system certificate validation."
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
            Button("Change server") { viewModel.startOver() }
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
            Text("Use your own account. New members first accept an invitation in the dashboard. This device appears on Devices and can be revoked there. There is no forgotten-password reset; keep your password in your password manager.")
        }
    }
}
