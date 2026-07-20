import SwiftUI

/// The Bills tab (M90/M95): recurring-bill suggestions to confirm, the current
/// bills with add/delete, a re-sync of linked accounts, and — when the income
/// analysis flags any — deposits to mark as income. Adults-only; every action
/// changes household money data, the same gate the server enforces.
struct BillsView: View {
    @Environment(AppModel.self) private var model
    @Bindable var viewModel: BillsViewModel
    @State private var addingBill = false
    @State private var editingBill: Components.Schemas.Bill?
    @State private var categorizing: Components.Schemas.Bill?

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.errorMessage != nil && viewModel.bills.isEmpty
                    && viewModel.billSuggestions.isEmpty
                {
                    ContentUnavailableView {
                        Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(viewModel.errorMessage ?? "")
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else {
                    list
                }
            }
            .navigationTitle("Bills")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { addingBill = true } label: {
                        Label("Add bill", systemImage: "plus")
                    }
                }
            }
            .sheet(isPresented: $addingBill) {
                BillFormView(viewModel: viewModel, mode: .add)
            }
            .sheet(item: $editingBill) { bill in
                BillFormView(viewModel: viewModel, mode: .edit(bill))
            }
            .sheet(item: $categorizing) { bill in
                CategoryPickerSheet(
                    title: bill.name,
                    categories: viewModel.categories,
                    currentCategoryID: bill.categoryId,
                    onSelect: { newID in
                        guard let id = newID,
                            let category = viewModel.categories.first(where: { $0.id == id })
                        else { return }
                        Task { await viewModel.setBillCategory(bill, to: category) }
                    },
                    onDelete: { category in Task { await viewModel.deleteCategory(id: category.id) } }
                )
            }
        }
        .task { await viewModel.load() }
    }

    private var list: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            if let syncResult = viewModel.syncResult {
                Label(syncResult, systemImage: "checkmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }

            // M111 (ADR 0024): the bill-paying headline — what's due vs cash on hand.
            if let timeline = viewModel.timeline {
                Section {
                    headline(timeline)
                }
            }

            // The payment timeline: every payment (bills, cards, loans, leases)
            // organized by time, not by category. Bills open their edit sheet.
            ForEach(viewModel.timelineSections, id: \.title) { section in
                Section {
                    ForEach(section.items, id: \.id) { item in
                        timelineRow(item)
                    }
                } header: {
                    Text(section.title)
                } footer: {
                    if section.title == "Paid this cycle" {
                        Text("Matched to the actual charge — tap to see the amount that settled it.")
                    } else if section.title == "No due date yet" {
                        Text("We haven't seen a payment on this account yet, so we can't infer its due day.")
                    }
                }
            }

            if !viewModel.billSuggestions.isEmpty {
                Section {
                    ForEach(viewModel.billSuggestions, id: \.merchantKey) { suggestion in
                        suggestionRow(suggestion)
                    }
                } header: {
                    Text("Suggested bills")
                } footer: {
                    Text("Recurring charges found in your spending. Swipe to add or dismiss.")
                }
            }

            if !viewModel.deposits.isEmpty {
                Section {
                    ForEach(viewModel.deposits, id: \.transactionId) { deposit in
                        depositRow(deposit)
                    }
                } header: {
                    Text("Deposits — income?")
                } footer: {
                    Text("Money that came in that we couldn't classify. Swipe to mark it.")
                }
            }

            // Add/edit/categorize/delete and the balance-sheet notes live one
            // level down, so the primary view stays a clean payment timeline.
            Section {
                NavigationLink {
                    ManageBillsView(
                        viewModel: viewModel,
                        editingBill: $editingBill,
                        categorizing: $categorizing
                    )
                } label: {
                    Label("Manage bills", systemImage: "slider.horizontal.3")
                }
                if viewModel.bills.isEmpty && !viewModel.isLoading {
                    Text("No bills yet. Add one with +, or confirm a suggestion.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
        }
        // Pull-to-refresh runs a full bank sync (then reloads), matching the
        // Accounts tab — one gesture, no separate refresh button.
        .refreshable {
            await viewModel.sync()
            model.syncStatus.markSynced()
        }
        .safeAreaInset(edge: .bottom) {
            SyncStatusFooter(status: model.syncStatus)
                .padding(.vertical, 6)
        }
    }

    /// "$8,254 due soon · $16,326 cash" — the bill-paying big picture (M111).
    private func headline(_ timeline: Components.Schemas.PaymentTimelineResponse) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(timeline.dueTotal.formattedExact)
                        .font(.system(.title, design: .rounded).weight(.semibold))
                    Text("due in the next \(timeline.windowDays) days")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(timeline.liquidBalance.formattedExact)
                        .font(.system(.title3, design: .rounded).weight(.medium))
                        .foregroundStyle(.secondary)
                    Text("cash on hand")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Label(
                timeline.covered
                    ? "Covered — your cash clears everything due."
                    : "Short — what's due exceeds your cash on hand.",
                systemImage: timeline.covered ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
            )
            .font(.caption.weight(.medium))
            .foregroundStyle(timeline.covered ? .green : .orange)
        }
        .padding(.vertical, 4)
    }

    /// One payment on the timeline. Bills open their edit sheet; account-backed
    /// rows (cards, loans, leases) are informational.
    @ViewBuilder
    private func timelineRow(_ item: Components.Schemas.PaymentTimelineItem) -> some View {
        let row = HStack(spacing: 12) {
            Image(systemName: Self.kindIcon(item.kind))
                .foregroundStyle(item.status == .overdue ? Color.red : Color.secondary)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name).lineLimit(1)
                Text(Self.statusLine(item))
                    .font(.caption)
                    .foregroundStyle(item.status == .overdue ? Color.red : Color.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(item.amount.formattedExact)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(item.status == .paid ? Color.secondary : Color.primary)
                if item.status == .paid {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            }
        }
        if item.kind == .bill, let bill = viewModel.bills.first(where: { $0.id == item.id }) {
            row
                .contentShape(Rectangle())
                .onTapGesture { editingBill = bill }
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteBill(bill) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                    Button {
                        categorizing = bill
                    } label: {
                        Label("Categorize", systemImage: "tag")
                    }
                    .tint(.accentColor)
                }
        } else {
            row
        }
    }

    static func kindIcon(_ kind: Components.Schemas.PaymentTimelineItem.KindPayload) -> String {
        switch kind {
        case .bill: return "doc.text"
        case .creditCard: return "creditcard"
        case .mortgage: return "house"
        case .loan: return "building.columns"
        case .lease: return "car"
        }
    }

    /// The row's one-line story: when it's due, or the receipt that settled it.
    static func statusLine(_ item: Components.Schemas.PaymentTimelineItem) -> String {
        switch item.status {
        case .paid:
            if let paid = item.paidWith {
                return "Paid \(shortDate(paid.occurredAt)) · \(paid.amount.formattedExact)"
                    + (item.dueDate.map { " · next \(shortDate($0))" } ?? "")
            }
            return "Paid"
        case .overdue:
            return "Was due \(shortDate(item.dueDate ?? "")) · no payment seen"
        case .dueSoon, .upcoming:
            let due = item.dueDate.map(shortDate) ?? "—"
            switch item.daysUntil {
            case .some(0): return "Due today"
            case .some(1): return "Due tomorrow"
            case .some(let days) where days > 1 && days <= 14: return "Due \(due) · in \(days) days"
            default: return "Due \(due)"
            }
        case .noDate:
            return item.kind == .creditCard ? "Current balance · due date unknown" : "Due date unknown"
        }
    }

    /// "2026-08-01" → "Aug 1".
    static func shortDate(_ iso: String) -> String {
        let parser = DateFormatter()
        parser.calendar = Calendar(identifier: .gregorian)
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: String(iso.prefix(10))) else { return iso }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }

    private func suggestionRow(_ suggestion: Components.Schemas.BillSuggestion) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(suggestion.name).lineLimit(1)
                Spacer()
                Text(suggestion.amount.formattedExact).font(.subheadline.weight(.medium))
            }
            Text(
                "\(Self.frequencyText(suggestion.frequency)) · seen \(suggestion.occurrences)× · "
                    + "next \(String(suggestion.nextDueDate.prefix(10)))"
            )
            .font(.caption).foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.confirmBill(suggestion) }
            } label: { Label("Add bill", systemImage: "checkmark") }
                .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.dismissBill(suggestion) }
            } label: { Label("Dismiss", systemImage: "xmark") }
        }
    }

    private func depositRow(_ deposit: Components.Schemas.IncomeAnalysisTransaction) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(deposit.merchant ?? deposit.name).lineLimit(1)
                Spacer()
                Text(deposit.amount.formattedExact)
                    .font(.subheadline.weight(.medium)).foregroundStyle(.green)
            }
            Text(
                String(deposit.occurredAt.prefix(10))
                    + (deposit.accountName.map { " · \($0)" } ?? "")
            )
            .font(.caption).foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.markIncome(deposit) }
            } label: { Label("Income", systemImage: "dollarsign.circle") }
                .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.markNotIncome(deposit) }
            } label: { Label("Not income", systemImage: "xmark") }
        }
    }

    static func frequencyText(_ frequency: Components.Schemas.RecurringFrequency) -> String {
        frequency.rawValue.capitalized
    }
}

/// The management level of the Bills tab (M111): the category-grouped bills with
/// edit/categorize/delete, and the account obligations with their balance-sheet
/// notes. The primary tab view stays a clean payment timeline; everything about
/// *defining* bills lives here.
struct ManageBillsView: View {
    @Bindable var viewModel: BillsViewModel
    @Binding var editingBill: Components.Schemas.Bill?
    @Binding var categorizing: Components.Schemas.Bill?

    var body: some View {
        List {
            if viewModel.bills.isEmpty {
                Section {
                    Text("No bills yet. Add one with +, or confirm a suggestion on the Bills tab.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            // Grouped by category (M96); a category sharing a title with an
            // obligation section renders as ONE section (M110).
            ForEach(viewModel.billSections) { section in
                Section {
                    ForEach(section.bills, id: \.id) { bill in
                        billRow(bill)
                            .contentShape(Rectangle())
                            .onTapGesture { editingBill = bill }
                            .swipeActions(edge: .trailing) {
                                Button(role: .destructive) {
                                    Task { await viewModel.deleteBill(bill) }
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                                Button {
                                    categorizing = bill
                                } label: {
                                    Label("Categorize", systemImage: "tag")
                                }
                                .tint(.accentColor)
                            }
                    }
                    ForEach(section.obligations, id: \.accountId) { obligation in
                        obligationRow(obligation)
                    }
                } header: {
                    Text(section.title)
                } footer: {
                    if let footer = section.obligationFooter {
                        Text(footer)
                    }
                }
            }
        }
        .navigationTitle("Manage bills")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func billRow(_ bill: Components.Schemas.Bill) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(bill.name).lineLimit(1)
                Text(
                    BillsView.frequencyText(bill.frequency)
                        + (bill.nextDueDate.map { " · next \(String($0.prefix(10)))" } ?? "")
                )
                .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(bill.amount.formattedExact).font(.subheadline.weight(.medium))
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
    }

    private func obligationRow(_ obligation: Components.Schemas.AccountObligation) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(obligation.name).lineLimit(1)
                Spacer()
                Text(obligation.amount.formattedExact)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(obligation.reserved ? .primary : .secondary)
            }
            HStack(spacing: 6) {
                Text("Monthly").font(.caption).foregroundStyle(.secondary)
                if !obligation.reserved {
                    Text("Not reserved")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.15), in: Capsule())
                        .foregroundStyle(.secondary)
                }
            }
            Text(obligation.note)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 2)
    }
}

/// Add or edit a bill by hand (M95/M110): name, amount, how often, when it's next
/// due, and its category. One form for both so the two flows stay identical (the
/// "uniform experience" rule) — the mode only changes the title, the button, and
/// whether fields start blank or pre-filled from an existing bill.
struct BillFormView: View {
    enum Mode {
        case add
        case edit(Components.Schemas.Bill)
    }

    @Environment(\.dismiss) private var dismiss
    let viewModel: BillsViewModel
    let mode: Mode

    @State private var name: String
    @State private var amount: Decimal?
    @State private var frequency: Components.Schemas.RecurringFrequency
    @State private var nextDue: Date
    @State private var categoryID: String  // "" = none
    private let currency: String

    // Bill scan: photo/PDF → candidate values prefill the fields below.
    @State private var showingCamera = false
    @State private var showingFileImporter = false
    @State private var scanning = false
    @State private var scanNote: String?

    init(viewModel: BillsViewModel, mode: Mode) {
        self.viewModel = viewModel
        self.mode = mode
        switch mode {
        case .add:
            _name = State(initialValue: "")
            _amount = State(initialValue: nil)
            _frequency = State(initialValue: .monthly)
            _nextDue = State(initialValue: Date())
            _categoryID = State(initialValue: "")
            currency = "USD"
        case .edit(let bill):
            _name = State(initialValue: bill.name)
            _amount = State(initialValue: Decimal(bill.amount.amountMinor) / 100)
            _frequency = State(initialValue: bill.frequency)
            _nextDue = State(initialValue: Self.parseDate(bill.nextDueDate) ?? Date())
            _categoryID = State(initialValue: bill.categoryId ?? "")
            currency = bill.amount.currency
        }
    }

    private var isEditing: Bool {
        if case .edit = mode { return true }
        return false
    }

    var body: some View {
        NavigationStack {
            Form {
                if !isEditing {
                    Section {
                        Menu {
                            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                                Button {
                                    showingCamera = true
                                } label: {
                                    Label("Take a photo", systemImage: "camera")
                                }
                            }
                            Button {
                                showingFileImporter = true
                            } label: {
                                Label("Choose a PDF or image", systemImage: "doc")
                            }
                            Button {
                                pasteBill()
                            } label: {
                                Label("Paste from clipboard", systemImage: "doc.on.clipboard")
                            }
                        } label: {
                            if scanning {
                                HStack(spacing: 6) { ProgressView(); Text("Reading bill…") }
                            } else {
                                Label("Scan a bill", systemImage: "doc.viewfinder")
                            }
                        }
                        .disabled(scanning)
                        if let scanNote {
                            Text(scanNote).font(.caption).foregroundStyle(.secondary)
                        }
                    } footer: {
                        Text("Photograph, upload, or paste a bill and the on-box vision model fills in what it can read. Confirm every value before saving.")
                    }
                }
                Section {
                    TextField("Name (e.g. Rent)", text: $name)
                    TextField("Amount", value: $amount, format: .currency(code: currency))
                        .keyboardType(.decimalPad)
                    Picker("Repeats", selection: $frequency) {
                        ForEach(Self.frequencies, id: \.self) { f in
                            Text(BillsView.frequencyText(f)).tag(f)
                        }
                    }
                    DatePicker("Next due", selection: $nextDue, displayedComponents: .date)
                    Picker("Category", selection: $categoryID) {
                        Text("None").tag("")
                        ForEach(viewModel.categories, id: \.id) { c in
                            Text(c.name).tag(c.id)
                        }
                    }
                }
            }
            .navigationTitle(isEditing ? "Edit bill" : "Add bill")
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isEditing ? "Save" : "Add") { save() }
                        .disabled(
                            name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                || (amount ?? 0) <= 0)
                }
            }
            .fullScreenCover(isPresented: $showingCamera) {
                CameraPicker { image in handleScan { await viewModel.scanBill(image) } }
                    .ignoresSafeArea()
            }
            .fileImporter(
                isPresented: $showingFileImporter,
                allowedContentTypes: [.pdf, .image]
            ) { result in
                guard case .success(let url) = result else { return }
                let isPDF = url.pathExtension.lowercased() == "pdf"
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                guard let data = try? Data(contentsOf: url) else { return }
                handleScan { await viewModel.scanBill(fileData: data, isPDF: isPDF) }
            }
        }
    }

    /// Scan a bill straight off the clipboard — a copied screenshot or PDF works
    /// exactly like a photographed/uploaded one (ADR 0028 pattern).
    private func pasteBill() {
        ClipboardImage.read { contents in
            switch contents {
            case .image(let image):
                handleScan { await viewModel.scanBill(image) }
            case .pdf(let data):
                handleScan { await viewModel.scanBill(fileData: data, isPDF: true) }
            case .none:
                scanNote = "There's no image or PDF on your clipboard to paste."
            }
        }
    }

    /// Prefill only — a scan never overwrites what the user already typed.
    private func handleScan(_ scan: @escaping () async -> Components.Schemas.BillScanResult?) {
        scanning = true
        Task {
            let result = await scan()
            scanning = false
            guard let result else { return }
            if let scanned = result.name, name.trimmingCharacters(in: .whitespaces).isEmpty {
                name = scanned
            }
            if let minor = result.amountMinor, amount == nil {
                amount = Decimal(minor) / 100
            }
            if let scanned = result.frequency,
                let mapped = Components.Schemas.RecurringFrequency(rawValue: scanned.rawValue)
            {
                frequency = mapped
            }
            if let due = Self.parseDate(result.nextDueDate) { nextDue = due }
            scanNote = result.note
        }
    }

    private func save() {
        var cents = (amount ?? 0) * 100
        var rounded = Decimal()
        NSDecimalRound(&rounded, &cents, 0, .plain)
        let minor = Int64(truncating: rounded as NSDecimalNumber)
        let due = Self.isoDate(nextDue)
        // "None" can only be sent on create; on edit the generated client omits a
        // nil category, so picking "None" keeps the current one (clearing is a
        // dashboard action) — same set-only constraint as Categorize.
        let category = categoryID.isEmpty ? nil : categoryID
        dismiss()
        Task {
            switch mode {
            case .add:
                await viewModel.addBill(
                    name: name, amountMinor: minor, currency: currency,
                    frequency: frequency, nextDueDate: due, categoryID: category)
            case .edit(let bill):
                await viewModel.editBill(
                    bill, name: name, amountMinor: minor, currency: currency,
                    frequency: frequency, nextDueDate: due, categoryID: category)
            }
        }
    }

    static let frequencies: [Components.Schemas.RecurringFrequency] =
        [.weekly, .biweekly, .semimonthly, .monthly, .quarterly, .annual]

    static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    /// Parse a `yyyy-MM-dd` (optionally longer ISO) date string to a `Date`.
    static func parseDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: String(value.prefix(10)))
    }
}

/// `Bill` carries a stable `id`; conforming lets it drive `.sheet(item:)`.
extension Components.Schemas.Bill: Identifiable {}
