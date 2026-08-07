import SwiftUI

/// Every device paired to this household (ADR 0025 parity with the dashboard's
/// Devices page). Revoking — swipe, `devices.manage` only — ends the device's
/// sessions AND destroys its wrap of the household data key (ADR 0072).
/// Pushed inside MoreView's stack: no NavigationStack of its own.
struct DevicesView: View {
    @State var viewModel: DevicesViewModel
    @State private var pendingRevoke: Components.Schemas.PairedDevice?

    var body: some View {
        List {
            Section {
                if viewModel.isLoading && viewModel.devices.isEmpty {
                    HStack { ProgressView(); Text("Loading…").padding(.leading, 8) }
                } else if viewModel.devices.isEmpty {
                    Text("No devices paired yet.").foregroundStyle(.secondary)
                }
                ForEach(viewModel.devices, id: \.id) { device in
                    deviceRow(device)
                }
            } footer: {
                Text(
                    "Each device holds a key that can unlock this household's data. Revoke anything you no longer recognize or use."
                )
            }
        }
        .navigationTitle("Devices")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        // Centered alert, not confirmationDialog — the anchored popover landed
        // far from the tapped row on settings-style screens (2026-07-25).
        .alert(
            pendingRevoke.map { "Revoke \($0.name)?" } ?? "Revoke this device?",
            isPresented: Binding(
                get: { pendingRevoke != nil },
                set: { if !$0 { pendingRevoke = nil } }),
            presenting: pendingRevoke
        ) { device in
            Button("Revoke", role: .destructive) {
                let target = device
                pendingRevoke = nil
                Task { await viewModel.revoke(target) }
            }
            Button("Cancel", role: .cancel) { pendingRevoke = nil }
        } message: { _ in
            Text(
                "Its sessions end and its key to this household's data is destroyed. The phone itself is untouched."
            )
        }
        .alert(
            "Couldn't complete",
            isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } })
        ) { Button("OK", role: .cancel) {} } message: { Text(viewModel.errorMessage ?? "") }
    }

    private func deviceRow(_ device: Components.Schemas.PairedDevice) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(verbatim: device.name)
                if viewModel.isCurrentDevice(device) {
                    Text("This device")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tint)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.tint.opacity(0.12), in: Capsule())
                }
                if device.revokedAt != nil {
                    Text("revoked")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.quaternary, in: Capsule())
                }
            }
            Text(Self.caption(for: device))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .opacity(device.revokedAt == nil ? 1 : 0.5)
        .swipeActions {
            // Revoke arms only from this explicit swipe — never a row tap
            // (same rule as the Backups rows). Absent for the current device:
            // revoking it would kill this very session (the server 409s too).
            if viewModel.canRevoke(device) {
                Button("Revoke", role: .destructive) {
                    pendingRevoke = device
                }
            }
        }
    }

    static func caption(for device: Components.Schemas.PairedDevice) -> String {
        let when = device.createdAt.formatted(date: .abbreviated, time: .omitted)
        guard let lastSeen = device.lastSeenAt else {
            return String(localized: "Paired \(when) · never used")
        }
        let seen = lastSeen.formatted(.relative(presentation: .named))
        return String(localized: "Paired \(when) · last seen \(seen)")
    }
}
