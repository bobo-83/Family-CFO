import SwiftUI

/// Role-aware shell (M83d). Advisor chat is the flagship tab, with the M88
/// daily-glance Overview beside it; operator features deliberately stay on the
/// web dashboard (mobile spec non-responsibilities).
struct MainTabView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    // Owned here, not in BillsView, so its pendingCount can drive the tab badge
    // and stay in sync the moment the screen clears an item (M90).
    @State private var billsModel: BillsViewModel?
    // Same reason (M97): the Review tab's badge tracks its own count live.
    @State private var reviewModel: ReviewViewModel?
    // Held here (not created inline in the Tab) so their loaded categories/goals
    // survive MainTabView re-renders — an inline view model resets to empty and
    // the category picker comes up blank.
    @State private var budgetsModel: BudgetsViewModel?
    @State private var goalsModel: GoalsViewModel?
    // M102: photos shared into the app via the Share Extension surface here.
    @State private var showSharedInbox = false

    var body: some View {
        TabView {
            // ADR 0034: every tab names the RIGHT that reveals it. Overview,
            // Accounts, and Debts are money VIEWS (all members); their editing
            // affordances gate separately inside each screen.
            if model.rolePolicy.canChat {
                Tab("Advisor", systemImage: "bubble.left.and.text.bubble.right") {
                    ConversationListView()
                }
            }
            Tab("Overview", systemImage: "chart.line.uptrend.xyaxis") {
                OverviewView()
            }
            if let accounts = model.accounts {
                Tab("Accounts", systemImage: "building.columns") {
                    AccountsView(viewModel: AccountsViewModel(api: accounts))
                }
            }
            if model.rolePolicy.canManageBills, let billsModel {
                Tab("Bills", systemImage: "calendar") {
                    BillsView(viewModel: billsModel)
                }
                .badge(billsModel.pendingCount)
            }
            if model.rolePolicy.canCategorize {
                Tab("Categories", systemImage: "tag") {
                    CategorizeView()
                }
            }
            if let debts = model.debts {
                Tab("Debts", systemImage: "banknote") {
                    NavigationStack { DebtsView(api: debts) }
                }
            }
            if model.rolePolicy.canCategorize, let reviewModel {
                Tab("Review", systemImage: "checklist") {
                    ReviewView(viewModel: reviewModel)
                }
                .badge(reviewModel.reviewCount)
            }
            if model.rolePolicy.canManageBudgets, let budgetsModel {
                Tab("Budgets", systemImage: "chart.pie") {
                    NavigationStack { BudgetsView(viewModel: budgetsModel) }
                }
            }
            if model.rolePolicy.canManageGoals, let goalsModel {
                Tab("Goals", systemImage: "target") {
                    NavigationStack { GoalsView(viewModel: goalsModel) }
                }
            }
            // Settings is never hidden — sign out lives here (ADR 0034).
            Tab("Settings", systemImage: "gearshape") {
                SettingsView()
            }
        }
        .sheet(isPresented: $showSharedInbox) {
            if let api = model.transactionDetail {
                SharedInboxAttachView(viewModel: SharedInboxViewModel(api: api))
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { checkSharedInbox() }
        }
        .task {
            if billsModel == nil, let api = model.bills {
                billsModel = BillsViewModel(api: api)
                await billsModel?.load()
            }
            if reviewModel == nil, let api = model.review {
                reviewModel = ReviewViewModel(api: api)
                await reviewModel?.load()
            }
            if budgetsModel == nil, let api = model.budgetsAPI {
                budgetsModel = BudgetsViewModel(api: api)
            }
            if goalsModel == nil, let api = model.goalsAPI {
                goalsModel = GoalsViewModel(api: api)
            }
            // M98: notify once if the latest backup (or its Synology copy) failed.
            if model.rolePolicy.isOperator, let backups = model.backups {
                await BackupFailureNotifier(api: backups).check()
            }
            checkSharedInbox()
        }
    }

    /// Surface the attach sheet when the Share Extension has dropped photos in and
    /// this member is allowed to edit finances. Dormant until the extension ships.
    private func checkSharedInbox() {
        guard model.rolePolicy.canEditFinances, SharedPhotoInbox.hasPending() else { return }
        showSharedInbox = true
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
                    // M120 (ADR 0029): one monorepo version everywhere; the app
                    // knows its own and shows it beside the server address.
                    LabeledContent("App version", value: OverviewViewModel.appVersion)
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
                if let aiStudy = model.aiStudy {
                    Section {
                        NavigationLink {
                            AdvisorKnowledgeView(viewModel: AiStudyViewModel(api: aiStudy))
                        } label: {
                            Label("Advisor knowledge", systemImage: "brain")
                        }
                    } header: {
                        Text("Advisor")
                    } footer: {
                        Text("How much of your history the AI has studied, and what it learned.")
                    }
                }
                if model.rolePolicy.canViewActivity || model.rolePolicy.canManageBackups {
                    Section {
                        if model.rolePolicy.canViewActivity, let activity = model.activity {
                            NavigationLink {
                                ActivityView(viewModel: ActivityViewModel(api: activity))
                            } label: {
                                Label("Activity", systemImage: "clock.arrow.circlepath")
                            }
                        }
                        if model.rolePolicy.canManageBackups, let backups = model.backups {
                            NavigationLink {
                                BackupSettingsView(viewModel: BackupViewModel(api: backups))
                            } label: {
                                Label("Backups", systemImage: "externaldrive")
                            }
                        }
                    } header: {
                        Text("Data")
                    } footer: {
                        Text("Review and undo past actions, and back up to your Synology. Encrypted daily backups run automatically.")
                    }
                }
                if model.rolePolicy.canManageMembers {
                    Section {
                        Label(
                            "Manage members, roles, devices and the AI runtime on the web dashboard.",
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
