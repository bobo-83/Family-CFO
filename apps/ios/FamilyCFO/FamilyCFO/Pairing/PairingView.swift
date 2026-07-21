import SwiftUI

/// First-run pairing screen (M83). The dashboard's Devices page shows a
/// one-time QR; scanning it (or pasting its payload when no camera is
/// available) leads to an explicit identity confirmation before the app
/// talks to the server.
struct PairingView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel = PairingViewModel()
    @State private var pastedPayload = ""
    /// ADR 0056: the QR-less path — sign in with email + password.
    @State private var showingLogin = false

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Pair with your CFO")
        }
        .sheet(isPresented: $showingLogin) {
            LoginView()
        }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
        case .scanning:
            scanStep
        case .confirming(let payload):
            confirmStep(payload)
        case .pairing:
            ProgressView("Pairing…")
        case .failed(let message):
            failureStep(message)
        }
    }

    private var scanStep: some View {
        VStack(spacing: 16) {
            Text("Open **Admin → Devices** on your Family CFO dashboard and generate a pairing QR code.")
                .font(.callout)
                .padding(.horizontal)

            if QRScannerView.isSupported {
                QRScannerView { viewModel.handleScanned($0) }
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .padding(.horizontal)
            } else {
                ContentUnavailableView(
                    "No camera available",
                    systemImage: "qrcode.viewfinder",
                    description: Text("Paste the QR payload below instead (shown under the QR on the dashboard).")
                )
            }

            VStack(spacing: 8) {
                TextField("…or paste the pairing payload", text: $pastedPayload, axis: .vertical)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .roundedField()
                Button("Use pasted payload") {
                    viewModel.handleScanned(pastedPayload)
                }
                .buttonStyle(.borderedProminent)
                .disabled(pastedPayload.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(.horizontal)

            // ADR 0056: members with their own credentials (e.g. joined via an
            // invite link) can sign in without a QR from the admin.
            Button("Sign in with email instead") {
                showingLogin = true
            }
            .font(.callout)

            Spacer()
        }
    }

    private func confirmStep(_ payload: PairingPayload) -> some View {
        Form {
            Section("You are pairing with") {
                LabeledContent("Household", value: payload.householdName)
                LabeledContent("Server", value: payload.apiBaseURL.absoluteString)
                if let fingerprint = payload.certificateSHA256 {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Certificate fingerprint")
                        Text(fingerprint)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Label(
                        "No certificate fingerprint in this code — the app will require a CA-trusted certificate.",
                        systemImage: "exclamationmark.shield"
                    )
                    .font(.caption)
                }
            }
            Section("This device") {
                TextField("Device name", text: Bindable(viewModel).deviceName)
            }
            Section {
                Button("Pair") {
                    Task { await viewModel.pair(payload: payload, into: model) }
                }
                Button("Cancel", role: .cancel) {
                    viewModel.cancelConfirmation()
                }
            } footer: {
                Text("Compare the fingerprint with the one shown on the dashboard. Pairing can be revoked there at any time.")
            }
        }
    }

    private func failureStep(_ message: String) -> some View {
        ContentUnavailableView {
            Label("Pairing failed", systemImage: "xmark.shield")
        } description: {
            Text(message)
        } actions: {
            Button("Try again") { viewModel.cancelConfirmation() }
                .buttonStyle(.borderedProminent)
        }
    }
}
