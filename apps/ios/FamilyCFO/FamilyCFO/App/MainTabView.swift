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

    enum MainTab: Hashable {
        case advisor, overview, accounts, bills, more
    }

    // Selection exists so other screens can steer here — the Year chart's
    // "explain this month" jumps to the Advisor tab (ADR 0068). If the role
    // can't chat, the Advisor tab is absent and SwiftUI shows the first tab.
    @State private var selectedTab: MainTab = .advisor

    var body: some View {
        TabView(selection: $selectedTab) {
            // ADR 0034: every tab names the RIGHT that reveals it. Overview,
            // Accounts, and Debts are money VIEWS (all members); their editing
            // affordances gate separately inside each screen.
            if model.rolePolicy.canChat {
                Tab("Advisor", systemImage: "bubble.left.and.text.bubble.right", value: MainTab.advisor) {
                    ConversationListView()
                }
            }
            Tab("Overview", systemImage: "chart.line.uptrend.xyaxis", value: MainTab.overview) {
                OverviewView()
            }
            if let accounts = model.accounts {
                Tab("Accounts", systemImage: "building.columns", value: MainTab.accounts) {
                    AccountsView(viewModel: AccountsViewModel(api: accounts))
                }
            }
            if model.rolePolicy.canManageBills, let billsModel {
                Tab("Bills", systemImage: "calendar", value: MainTab.bills) {
                    BillsView(viewModel: billsModel)
                }
                .badge(billsModel.pendingCount)
            }
            // Everything else lives in OUR More tab (one NavigationStack).
            // With more tabs than fit, iOS used to collapse the overflow into
            // the SYSTEM More tab, which wraps its own navigation controller
            // around screens that already own a stack — two nav bars and two
            // back buttons (user report 2026-07-22). Settings is in here and
            // never hidden — sign out lives there (ADR 0034).
            Tab("More", systemImage: "ellipsis.circle", value: MainTab.more) {
                MoreView(
                    reviewModel: reviewModel,
                    budgetsModel: budgetsModel,
                    goalsModel: goalsModel
                )
            }
            .badge(reviewModel?.reviewCount ?? 0)
        }
        .onChange(of: model.advisorAsk) { _, ask in
            guard ask != nil, model.rolePolicy.canChat else { return }
            selectedTab = .advisor
        }
        .sheet(isPresented: $showSharedInbox) {
            if let api = model.transactionDetail {
                SharedInboxAttachView(viewModel: SharedInboxViewModel(api: api))
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                checkSharedInbox()
                // ADR 0067 v6: keep the watch's pairing copy fresh.
                model.pushWatchPairing()
            }
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

/// Our own overflow tab: ONE NavigationStack hosting every secondary screen,
/// so nothing ever lands in the system More tab's extra navigation controller
/// (which stacked a second nav bar over screens that own one — the
/// double-back-button report, 2026-07-22). Pushed screens must NOT create
/// their own stack; sheets inside them still may.
private struct MoreView: View {
    @Environment(AppModel.self) private var model
    let reviewModel: ReviewViewModel?
    let budgetsModel: BudgetsViewModel?
    let goalsModel: GoalsViewModel?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if model.rolePolicy.canManageIncome, let income = model.income {
                        NavigationLink {
                            IncomeView(viewModel: IncomeViewModel(api: income))
                        } label: {
                            Label("Income", systemImage: "dollarsign.circle")
                        }
                    }
                    if model.rolePolicy.canCategorize {
                        NavigationLink {
                            CategorizeView()
                        } label: {
                            Label("Categories", systemImage: "tag")
                        }
                    }
                    if let debts = model.debts {
                        NavigationLink {
                            DebtsView(api: debts)
                        } label: {
                            Label("Debts", systemImage: "banknote")
                        }
                    }
                    if model.rolePolicy.canCategorize, let reviewModel {
                        NavigationLink {
                            ReviewView(viewModel: reviewModel)
                        } label: {
                            Label("Review", systemImage: "checklist")
                                .badge(reviewModel.reviewCount)
                        }
                    }
                    if model.rolePolicy.canManageBudgets, let budgetsModel {
                        NavigationLink {
                            BudgetsView(viewModel: budgetsModel)
                        } label: {
                            Label("Budgets", systemImage: "chart.pie")
                        }
                    }
                    if model.rolePolicy.canManageGoals, let goalsModel {
                        NavigationLink {
                            GoalsView(viewModel: goalsModel)
                        } label: {
                            Label("Goals", systemImage: "target")
                        }
                    }
                }
                Section {
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Label("Settings", systemImage: "gearshape")
                    }
                }
            }
            .navigationTitle("More")
        }
    }
}

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmingUnpair = false
    @State private var confirmingSignOut = false
    @AppStorage("family-cfo.showAdvisorDisclaimer") private var showDisclaimer = true

    // No NavigationStack of its own: pushed inside MoreView's stack — a second
    // stack here is exactly what doubled the nav bars (2026-07-22).
    var body: some View {
        Group {
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
                        if let aiRuntime = model.aiRuntime {
                            NavigationLink {
                                AIRuntimeView(viewModel: AIRuntimeViewModel(api: aiRuntime))
                            } label: {
                                Label("AI runtime", systemImage: "cpu")
                            }
                        }
                        Toggle(isOn: $showDisclaimer) {
                            Label("Show advisor disclaimer", systemImage: "text.badge.checkmark")
                        }
                    } header: {
                        Text("Advisor")
                    } footer: {
                        Text("What the AI has studied, and which model answers. Hiding the disclaimer only tucks the reminder away — the advisor stays educational guidance, not financial advice (ADR 0031).")
                    }
                }
                // Always present: Devices is listed for every member (only its
                // revoke gates on devices.manage, inside the screen).
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
                    if let devices = model.devices {
                        NavigationLink {
                            DevicesView(viewModel: DevicesViewModel(
                                api: devices,
                                canRevoke: model.rolePolicy.canManageDevices,
                                currentDeviceID: model.credential?.deviceID))
                        } label: {
                            Label("Devices", systemImage: "iphone.radiowaves.left.and.right")
                        }
                    }
                    // #180: operator hosting — only system admins ever see it.
                    if model.rolePolicy.canHostHouseholds,
                        let households = model.hostedHouseholds,
                        let server = model.server
                    {
                        NavigationLink {
                            HouseholdsView(viewModel: HouseholdsViewModel(
                                api: households,
                                serverBaseURL: server.apiBaseURL,
                                currentHouseholdID: server.householdID))
                        } label: {
                            Label("Households", systemImage: "building.2")
                        }
                    }
                } header: {
                    Text("Data")
                } footer: {
                    Text(
                        model.rolePolicy.isOperator
                            ? "Review and undo past actions, back up to your Synology, and see every device paired to this household. Encrypted daily backups run automatically."
                            : "Every device paired to this household."
                    )
                }
                if model.rolePolicy.canManageSystemAdmins, let roster = model.systemAdmins {
                    Section {
                        NavigationLink {
                            SystemAdminsView(viewModel: SystemAdminsViewModel(api: roster))
                        } label: {
                            Label("System administrators", systemImage: "person.badge.key")
                        }
                    } footer: {
                        Text("Box-level operators (ADR 0065): who may swap the AI model and manage whole-box backups, across every household.")
                    }
                }
                if model.rolePolicy.canManageMembers {
                    Section {
                        Label(
                            "Manage members and roles on the web dashboard.",
                            systemImage: "wrench.and.screwdriver"
                        )
                        .font(.callout)
                    }
                }
                Section {
                    Button("Sign out") {
                        confirmingSignOut = true
                    }
                } footer: {
                    Text("Signs this member out but keeps the server pairing info — sign back in with email + password (ADR 0056), or scan a fresh QR. Good for switching members on a shared device.")
                }
                Section {
                    Button("Unpair this device", role: .destructive) {
                        confirmingUnpair = true
                    }
                } footer: {
                    Text("Removes the credential AND the server info from this phone. To revoke it server-side too, use the Devices screen (here or on the dashboard).")
                }
            }
            .navigationTitle("Settings")
            // Centered alerts, not confirmationDialog: on this screen the
            // dialog rendered as a popover anchored far from the tapped row
            // (user report 2026-07-25) — a modal in the middle is unambiguous.
            .alert(
                "Sign out?",
                isPresented: $confirmingSignOut
            ) {
                Button("Sign out") { Task { await model.signOut() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The server address and pinned certificate stay on this phone; only your session ends.")
            }
            .alert(
                "Unpair this device?",
                isPresented: $confirmingUnpair
            ) {
                Button("Unpair", role: .destructive) { model.unpair() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Removes the credential and the server info from this phone.")
            }
        }
    }
}
