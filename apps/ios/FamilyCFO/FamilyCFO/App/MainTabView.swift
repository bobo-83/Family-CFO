import SwiftUI

/// Role-aware shell (M83d). Advisor chat is the flagship tab, with the M88
/// daily-glance Overview beside it; operator features deliberately stay on the
/// web dashboard (mobile spec non-responsibilities).
struct MainTabView: View {
    @Environment(AppModel.self) private var model
    // Owned here, not in BillsView, so its pendingCount can drive the tab badge
    // and stay in sync the moment the screen clears an item (M90).
    @State private var billsModel: BillsViewModel?

    var body: some View {
        TabView {
            Tab("Advisor", systemImage: "bubble.left.and.text.bubble.right") {
                ConversationListView()
            }
            Tab("Overview", systemImage: "chart.line.uptrend.xyaxis") {
                OverviewView()
            }
            // Bills and categorize both change money data (M90/M91), so they're
            // for the adults — the same gate the server enforces on the writes.
            if model.rolePolicy.canEditFinances {
                if let billsModel {
                    Tab("Bills", systemImage: "calendar") {
                        BillsView(viewModel: billsModel)
                    }
                    .badge(billsModel.pendingCount)
                }
                Tab("Categorize", systemImage: "tag") {
                    CategorizeView()
                }
            }
            Tab("Settings", systemImage: "gearshape") {
                SettingsView()
            }
        }
        .task {
            if billsModel == nil, let api = model.bills {
                billsModel = BillsViewModel(api: api)
                await billsModel?.load()
            }
        }
    }
}

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmingUnpair = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Household") {
                    LabeledContent("Name", value: model.server?.householdName ?? "—")
                    LabeledContent("Acting as", value: model.rolePolicy.displayName)
                }
                Section {
                    LabeledContent("Address", value: model.server?.apiBaseURL.absoluteString ?? "—")
                    if let fingerprint = model.server?.certificateSHA256 {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Pinned certificate")
                            Text(fingerprint)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("Server")
                } footer: {
                    Text("Away from home, connect through your household's own VPN or tailnet — the server is never exposed to the internet.")
                }
                if model.rolePolicy.isOperator {
                    Section {
                        Label(
                            "Manage paired devices, members, backups and the AI runtime on the web dashboard.",
                            systemImage: "wrench.and.screwdriver"
                        )
                        .font(.callout)
                    }
                }
                Section {
                    Button("Unpair this device", role: .destructive) {
                        confirmingUnpair = true
                    }
                } footer: {
                    Text("Removes the credential from this phone. To revoke it server-side too, use the dashboard's Devices page.")
                }
            }
            .navigationTitle("Settings")
            .confirmationDialog(
                "Unpair this device?",
                isPresented: $confirmingUnpair,
                titleVisibility: .visible
            ) {
                Button("Unpair", role: .destructive) { model.unpair() }
            }
        }
    }
}
