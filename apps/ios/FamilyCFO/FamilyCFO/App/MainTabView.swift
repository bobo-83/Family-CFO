import SwiftUI

/// Role-aware shell (M83d). Advisor chat is the flagship tab, with the M88
/// daily-glance Overview beside it; operator features deliberately stay on the
/// web dashboard (mobile spec non-responsibilities).
struct MainTabView: View {
    @Environment(AppModel.self) private var model
    // Owned here, not in ReviewView, so its pendingCount can drive the tab badge
    // and stay in sync the moment the screen clears an item (M90).
    @State private var reviewModel: ReviewViewModel?

    var body: some View {
        TabView {
            Tab("Advisor", systemImage: "bubble.left.and.text.bubble.right") {
                ConversationListView()
            }
            Tab("Overview", systemImage: "chart.line.uptrend.xyaxis") {
                OverviewView()
            }
            // Review and categorize both change money data (M90/M91), so they're
            // for the adults — the same gate the server enforces on the writes.
            if model.rolePolicy.canEditFinances {
                if let reviewModel {
                    Tab("Review", systemImage: "tray.full") {
                        ReviewView(viewModel: reviewModel)
                    }
                    .badge(reviewModel.pendingCount)
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
            if reviewModel == nil, let api = model.review {
                reviewModel = ReviewViewModel(api: api)
                await reviewModel?.load()
            }
        }
    }
}

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmingUnpair = false
    @AppStorage(VoicePreference.storageKey) private var voiceRaw = VoicePreference.default.rawValue

    private var voice: Binding<VoicePreference> {
        Binding(
            get: { VoicePreference(rawValue: voiceRaw) ?? .default },
            set: { voiceRaw = $0.rawValue })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Household") {
                    LabeledContent("Name", value: model.server?.householdName ?? "—")
                    LabeledContent("Acting as", value: model.rolePolicy.displayName)
                }
                Section {
                    Picker("Voice", selection: voice) {
                        ForEach(VoicePreference.allCases) { option in
                            Text(option.title).tag(option)
                        }
                    }
                } header: {
                    Text("Voice")
                } footer: {
                    Text(voice.wrappedValue.detail)
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
