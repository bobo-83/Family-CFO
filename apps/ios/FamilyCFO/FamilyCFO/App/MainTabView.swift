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
    // Held here so the loaded language survives Form re-renders (same reason
    // MainTabView owns its tab view models).
    @State private var languageModel: HouseholdLanguageViewModel?
    // Held here for the same reason as languageModel — survives Form re-renders.
    @State private var reserveModel: CommittedSavingsReserveViewModel?
    // #41: same reason again — and the picker it pushes reads it back.
    @State private var timezoneModel: HouseholdTimezoneViewModel?
    // #96: the sealed-mode offer, beside the language and time-zone rows.
    @State private var sealOfferModel: SealedModeOfferViewModel?
    // ADR 0074: the box's own version, shown beside this build's. Builds now
    // move independently, so "App version" alone no longer says what the phone
    // is talking to. Best-effort — nil (shown as "—") when the box is
    // unreachable, exactly like the Overview banner's guard.
    @State private var boxVersion: String?
    @AppStorage("family-cfo.showAdvisorDisclaimer") private var showDisclaimer = true
    // Per-device (deliberately NOT a household setting — it's about this
    // phone's speaker, battery, and taste): speak English answers in the
    // phone's built-in voice instead of the box's natural voice. Read per
    // utterance by the synthesizer, so flipping it mid-session is heard on
    // the very next answer.
    @AppStorage(FallbackSpeechSynthesizer.prefersOnDeviceVoiceKey)
    private var preferOnDeviceVoice = false

    // No NavigationStack of its own: pushed inside MoreView's stack — a second
    // stack here is exactly what doubled the nav bars (2026-07-22).
    var body: some View {
        Group {
            Form {
                Section {
                    LabeledContent("Name", value: model.server?.householdName ?? "—")
                    LabeledContent("Acting as", value: model.rolePolicy.displayName)
                    if let languageModel {
                        // #10: household-wide (one language per household, a
                        // server constraint) — so only household.settings.manage,
                        // the right the PATCH checks, may change it.
                        if model.rolePolicy.canManageHouseholdSettings {
                            Picker(
                                "Language",
                                selection: Binding(
                                    get: { languageModel.language },
                                    set: { code in
                                        Task {
                                            await languageModel.change(to: code)
                                            // Post-change (it rolls back on
                                            // failure): the speech paths read
                                            // this per utterance (#10 phase 1).
                                            model.householdLanguage = languageModel.language
                                        }
                                    }
                                )
                            ) {
                                ForEach(HouseholdLanguageViewModel.options) { option in
                                    // A language's own name is never translated.
                                    Text(verbatim: option.name).tag(option.code)
                                }
                            }
                        } else {
                            LabeledContent("Language", value: languageModel.displayName)
                        }
                    }
                    if let timezoneModel {
                        // #41: household-wide — the server reckons every date
                        // in this zone, so only household.settings.manage, the
                        // right the PATCH checks, may change it.
                        if model.rolePolicy.canManageHouseholdSettings {
                            NavigationLink {
                                HouseholdTimezonePicker(model: timezoneModel)
                            } label: {
                                LabeledContent("Time zone", value: timezoneModel.displayName)
                            }
                        } else {
                            LabeledContent("Time zone", value: timezoneModel.displayName)
                        }
                    }
                    if let sealOfferModel, sealOfferModel.isOffered {
                        sealedModeOffer(sealOfferModel)
                    }
                } header: {
                    Text("Household")
                } footer: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("The advisor answers in this language. App screens follow in a later update.")
                        // Ground truth for "why doesn't it sound right": the
                        // voice the app RESOLVED on this phone, or the fact
                        // that none is installed. Settings can list a default
                        // voice whose asset was never downloaded — the app
                        // only sees voices on disk.
                        if let languageModel, languageModel.language != "en" {
                            Text(SpeechSynthesizerService.voiceStatus(for: languageModel.language))
                                .foregroundStyle(.secondary)
                        }
                        if let error = languageModel?.errorMessage {
                            Text(error).foregroundStyle(.red)
                        }
                        Text("Bills, due dates and Safe to Spend use this zone to decide what “today” means.")
                        if let error = timezoneModel?.errorMessage {
                            Text(error).foregroundStyle(.red)
                        }
                    }
                }
                if let reserveModel {
                    Section {
                        // #5: household-wide — reserving changes the shared
                        // safe_to_spend computation, so only household.settings.manage
                        // (the right the PATCH checks) may change it.
                        if model.rolePolicy.canManageHouseholdSettings {
                            Toggle(
                                "Reserve committed savings",
                                isOn: Binding(
                                    get: { reserveModel.reserved },
                                    set: { on in Task { await reserveModel.setReserved(on) } }
                                )
                            )
                        } else {
                            LabeledContent(
                                "Reserve committed savings",
                                value: reserveModel.reserved
                                    ? String(localized: "On") : String(localized: "Off"))
                        }
                    } footer: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Off: shown beside Safe to Spend. On: subtracted like a bill.")
                            if let error = reserveModel.errorMessage {
                                Text(error).foregroundStyle(.red)
                            }
                        }
                    }
                }
                Section {
                    // M120 (ADR 0029, amended by ADR 0074): both sides report
                    // "<contract>.<build>", and they are compatible when the
                    // contract — the leading MAJOR.MINOR — matches. A differing
                    // build here is normal and is not warned about; the Overview
                    // banner fires only on a contract difference.
                    LabeledContent("App version", value: OverviewViewModel.appVersion)
                    LabeledContent("Box version", value: boxVersion ?? "—")
                    LabeledContent("Address", value: model.server?.apiBaseURL.absoluteString ?? "—")
                    if let fingerprint = model.server?.certificateSHA256 {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Pinned certificate")
                            Text(verbatim: fingerprint)
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
                        Toggle(isOn: $preferOnDeviceVoice) {
                            Label("Use this iPhone's voice", systemImage: "speaker.wave.2")
                        }
                    } header: {
                        Text("Advisor")
                    } footer: {
                        Text("What the AI has studied, and which model answers. Hiding the disclaimer only tucks the reminder away — the advisor stays educational guidance, not financial advice (ADR 0031). English answers default to the box's natural voice when it's available; switch on this iPhone's voice to hear them in the phone's built-in voice instead — non-English answers always use it. A choice for this phone only.")
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
                // #97: your own password. No role gate — everyone has one, and
                // a password nobody can retire is a password nobody can share
                // by accident and take back.
                if let passwords = model.changePassword {
                    Section {
                        NavigationLink {
                            ChangePasswordView(
                                viewModel: ChangePasswordViewModel(api: passwords))
                        } label: {
                            Label("Change password", systemImage: "key")
                        }
                    } header: {
                        Text("Account")
                    } footer: {
                        Text("Changing your password signs out every other browser and phone signed in as you. This phone stays signed in.")
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
            // Its own task, not a step in the one below: a /health round-trip
            // must not delay the language, reserve and time-zone loads that
            // actually render rows.
            .task {
                if boxVersion == nil, let api = model.household {
                    boxVersion = await api.serverVersion()
                }
            }
            .task {
                if languageModel == nil, let api = model.household {
                    languageModel = HouseholdLanguageViewModel(api: api)
                    await languageModel?.load()
                }
                if reserveModel == nil, let api = model.household {
                    reserveModel = CommittedSavingsReserveViewModel(api: api)
                    await reserveModel?.load()
                }
                if timezoneModel == nil, let api = model.household {
                    timezoneModel = HouseholdTimezoneViewModel(api: api)
                    await timezoneModel?.load()
                }
                // #96: only members who could actually seal — GET
                // /household/key-status is gated on backups.manage, so anyone
                // else would get a 403 and an offer they cannot accept.
                if sealOfferModel == nil, model.rolePolicy.canManageBackups,
                    let api = model.backups
                {
                    sealOfferModel = SealedModeOfferViewModel(api: api)
                    await sealOfferModel?.load()
                }
            }
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

    /// #96 (ADR 0072 Phase 3): sealed mode was reachable only by scrolling the
    /// Backups screen. Offered here — never switched on for anyone — with the
    /// price stated plainly, and dismissible for good on this phone.
    @ViewBuilder private func sealedModeOffer(_ offer: SealedModeOfferViewModel) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Seal this household", systemImage: "lock.shield")
                .font(.subheadline.weight(.semibold))
            Text(
                "The box keeps a spare of your household key so it can work while nobody is here. Sealing removes that spare: your key then exists only while a member is signed in, so an offline copy — a stolen disk, a backup archive, a snapshot — is unreadable."
            )
            Text(
                "It guards against anything that reaches your data without a session: the machine, its disks, its backups, another household sharing the box."
            )
            Text(
                "It is off by default because the price is real. Unattended work — bank sync, snapshots, imports, reports, idle study — then runs only while someone is signed in, because the box holds no key of its own. It catches up when you sign in and pauses when the last of you signs out, so a day nobody visits leaves a gap in the trend. And losing the recovery key with nobody signed in loses the data — no one can override that."
            )
            if offer.needsRecoveryKey {
                Text("Before you can seal: create a recovery key.")
                    .foregroundStyle(.orange)
            }
            if offer.needsMemberKey {
                Text(
                    "Before you can seal: sign in with your password once — pairing a phone does not make a member key."
                )
                .foregroundStyle(.orange)
            }
        }
        .font(.caption)
        if let backups = model.backups {
            NavigationLink {
                BackupSettingsView(viewModel: BackupViewModel(api: backups))
            } label: {
                Label("Privacy mode", systemImage: "lock.rotation")
            }
        }
        Button("Dismiss on this iPhone") { offer.dismiss() }
    }
}

/// #41: picking the household's zone. The device knows hundreds of them, so the
/// list opens on a shortlist — whatever is set, then this phone's own zone, then
/// the common ones — and search reaches everything else.
struct HouseholdTimezonePicker: View {
    let model: HouseholdTimezoneViewModel
    @State private var query = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            // #43: the way back to inheriting the box's own zone. Without it
            // the inherit state is one-way — reachable only by never having
            // picked a zone at all.
            if model.offersBoxDefault(matching: query) {
                Button {
                    Task {
                        await model.change(to: nil)
                        dismiss()
                    }
                } label: {
                    Text("Use the box's zone")
                }
                .foregroundStyle(.primary)
            }
            ForEach(model.options(matching: query), id: \.self) { zone in
                Button {
                    Task {
                        // Leaves immediately: a rejected zone rolls back and says
                        // so in the Settings footer we're returning to.
                        await model.change(to: zone)
                        dismiss()
                    }
                } label: {
                    HStack {
                        // A zone ID is an identifier, never translated.
                        Text(verbatim: zone)
                        Spacer()
                        if zone == model.timezone {
                            Image(systemName: "checkmark")
                                .foregroundStyle(Color.accentColor)
                        }
                    }
                }
                .foregroundStyle(.primary)
            }
        }
        .searchable(text: $query, prompt: Text("Search zones"))
        .navigationTitle("Time zone")
    }
}
