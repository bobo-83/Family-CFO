import SwiftUI
import UIKit

/// Every family this box hosts (#180, ADR 0025 parity with the dashboard's
/// Households page). System admins only — the Settings entry is gated on
/// `system.admin`. Pushed inside MoreView's stack: no NavigationStack of its own.
struct HouseholdsView: View {
    @State var viewModel: HouseholdsViewModel
    @State private var creating = false
    /// #189: the armed row — a delete only ever starts from its swipe action,
    /// never a row tap, and always through this confirmation.
    @State private var pendingDelete: Components.Schemas.HostedHousehold?

    var body: some View {
        List {
            Section {
                if viewModel.isLoading && viewModel.households.isEmpty {
                    HStack { ProgressView(); Text("Loading…").padding(.leading, 8) }
                } else if viewModel.households.isEmpty {
                    Text("No hosted households yet.").foregroundStyle(.secondary)
                }
                ForEach(viewModel.households, id: \.id) { household in
                    householdRow(household)
                }
            } footer: {
                Text(
                    "Each household's data stays its own — you host the box, every family runs its own household."
                )
            }
        }
        .navigationTitle("Households")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .toolbar {
            Button("Create household…") { creating = true }
        }
        .sheet(isPresented: $creating, onDismiss: { viewModel.dismissInvite() }) {
            CreateHouseholdSheet(viewModel: viewModel)
        }
        .alert(
            "Couldn't complete",
            isPresented: Binding(
                // The sheet shows create errors itself; this alert covers
                // loads and deletes (the 409/404 detail, verbatim).
                get: { viewModel.errorMessage != nil && !creating },
                set: { if !$0 { viewModel.errorMessage = nil } })
        ) { Button("OK", role: .cancel) {} } message: { Text(viewModel.errorMessage ?? "") }
        .alert(
            "Permanently delete \(pendingDelete?.name ?? "")?",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }),
            presenting: pendingDelete
        ) { household in
            Button("Delete household", role: .destructive) {
                let target = household
                pendingDelete = nil
                Task { await viewModel.delete(target) }
            }
            Button("Cancel", role: .cancel) { pendingDelete = nil }
        } message: { _ in
            let days = viewModel.offboxRetentionDays
            let horizon =
                days > 0
                ? "Their data remains only in encrypted backups, fully gone within \(days) days."
                : "Their data remains in encrypted off-box backups until you prune them."
            return Text(
                "This removes the family's accounts, transactions, advisor history, documents, and logins. It cannot be undone. " + horizon
            )
        }
    }

    private func householdRow(_ household: Components.Schemas.HostedHousehold) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(household.name)
                if household.pendingOwnerInvite {
                    Text("invite pending")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tint)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.tint.opacity(0.12), in: Capsule())
                }
                if household.sealed {
                    Text("sealed")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.quaternary, in: Capsule())
                }
            }
            Text(Self.caption(for: household))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing) {
            // #189: never on the operator's own household. The swipe only ARMS
            // the confirmation — nothing is deleted until it's confirmed.
            if viewModel.canDelete(household) {
                Button("Delete…") {
                    pendingDelete = household
                }
                .tint(.red)
            }
        }
    }

    static func caption(for household: Components.Schemas.HostedHousehold) -> String {
        let members =
            household.memberCount == 1 ? "1 member" : "\(household.memberCount) members"
        let created = household.createdAt.formatted(date: .abbreviated, time: .omitted)
        return "\(household.baseCurrency) · \(members) · created \(created)"
    }
}

/// Three fields in, one-time join link out. The link stays on screen until the
/// operator leaves — it can never be retrieved again.
private struct CreateHouseholdSheet: View {
    @Bindable var viewModel: HouseholdsViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var displayName = ""
    @State private var baseCurrency = "USD"
    @State private var ownerEmail = ""
    @State private var copied = false

    private var canSubmit: Bool {
        !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && baseCurrency.trimmingCharacters(in: .whitespacesAndNewlines).count == 3
            && ownerEmail.contains("@")
    }

    var body: some View {
        NavigationStack {
            Form {
                if let invite = viewModel.invite {
                    inviteSection(invite)
                } else {
                    fieldsSection
                }
            }
            .navigationTitle("Create household")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .alert(
                "Couldn't complete",
                isPresented: Binding(
                    get: { viewModel.errorMessage != nil },
                    set: { if !$0 { viewModel.errorMessage = nil } })
            ) { Button("OK", role: .cancel) {} } message: { Text(viewModel.errorMessage ?? "") }
        }
    }

    private var fieldsSection: some View {
        Group {
            Section {
                TextField("Family name", text: $displayName)
                TextField("Currency (3-letter)", text: $baseCurrency)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                TextField("Owner email", text: $ownerEmail)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } footer: {
                Text(
                    "The owner gets a one-time join link — they pick their own password on the dashboard and run the household themselves."
                )
            }
            Section {
                Button {
                    Task {
                        _ = await viewModel.create(
                            displayName: displayName,
                            baseCurrency: baseCurrency,
                            ownerEmail: ownerEmail)
                    }
                } label: {
                    if viewModel.isCreating {
                        HStack { ProgressView(); Text("Creating…").padding(.leading, 8) }
                    } else {
                        Text("Create household")
                    }
                }
                .disabled(!canSubmit || viewModel.isCreating)
            }
        }
    }

    private func inviteSection(_ invite: HouseholdInvite) -> some View {
        Section {
            Label(
                "Shown once — share it with the family now.",
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.caption)
            .foregroundStyle(.orange)
            Text(invite.joinURL.absoluteString)
                .font(.footnote.monospaced())
                .textSelection(.enabled)
            Button {
                UIPasteboard.general.string = invite.joinURL.absoluteString
                copied = true
            } label: {
                Label(copied ? "Copied" : "Copy join link", systemImage: "doc.on.doc")
            }
            ShareLink(item: invite.joinURL) {
                Label("Share link…", systemImage: "square.and.arrow.up")
            }
        } header: {
            Text(invite.householdName)
        } footer: {
            Text(
                "Send it to \(invite.ownerEmail). It expires \(invite.expiresAt.formatted(date: .abbreviated, time: .omitted))."
            )
        }
    }
}
