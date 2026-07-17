import SwiftUI
import UniformTypeIdentifiers

/// Add, edit, and review loans — mortgage, auto, student, 401(k), other (M96).
/// The monthly payment flows into safe-to-spend; the balance into net worth and
/// total debt (a 401(k) loan is netted against retirement).
struct DebtsView: View {
    @State private var viewModel: DebtsViewModel
    @State private var addingLoan = false
    @State private var editing: LoanEdit?

    init(api: DebtsAPI) {
        _viewModel = State(initialValue: DebtsViewModel(api: api))
    }

    /// Identifiable wrapper so a tapped loan can drive `.sheet(item:)`.
    private struct LoanEdit: Identifiable {
        let loan: Components.Schemas.Account
        var id: String { loan.id }
    }

    var body: some View {
        List {
            if !viewModel.loans.isEmpty {
                Section {
                    LabeledContent("Total owed", value: money(viewModel.totalOwed))
                    LabeledContent("Monthly payments", value: money(viewModel.totalMonthly))
                } footer: {
                    Text("A loan's monthly payment counts against safe-to-spend and its balance against net worth — except 401(k) loans, which are payroll-deducted and net against your retirement, so they affect neither. \"Monthly payments\" above excludes them.")
                }
            }

            Section {
                ForEach(viewModel.loans, id: \.id) { loan in
                    Button { editing = LoanEdit(loan: loan) } label: { row(loan) }
                        .buttonStyle(.plain)
                }
                .onDelete { indexSet in
                    let targets = indexSet.map { viewModel.loans[$0] }
                    Task { for loan in targets { await viewModel.deleteLoan(loan) } }
                }
                Button {
                    addingLoan = true
                } label: {
                    Label("Add a loan", systemImage: "plus.circle.fill")
                }
            } header: {
                Text("Loans")
            } footer: {
                Text("Loans differ from credit cards: you pay a fixed amount each month, not the full balance. Tap one to edit its details or mark it paid off. Enter the payment and what you still owe.")
            }
        }
        .navigationTitle("Debts & loans")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if viewModel.loans.isEmpty && !viewModel.isLoading {
                ContentUnavailableView {
                    Label("No loans yet", systemImage: "banknote")
                } description: {
                    Text("Add a mortgage, auto, student, 401(k), or other loan to track its payment and balance.")
                } actions: {
                    Button("Add a loan") { addingLoan = true }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .sheet(isPresented: $addingLoan) {
            LoanFormSheet(
                existing: nil,
                currency: viewModel.currency,
                onSave: { draft in await viewModel.addLoan(draft) },
                onScan: { image in await viewModel.scanStatement(image) },
                onScanFile: { data, isPDF in await viewModel.scanStatement(fileData: data, isPDF: isPDF) },
                onDelete: nil
            )
        }
        .sheet(item: $editing) { edit in
            LoanFormSheet(
                existing: edit.loan,
                currency: edit.loan.balance.currency,
                onSave: { draft in await viewModel.updateLoan(id: edit.loan.id, draft) },
                onScan: { image in await viewModel.scanStatement(image) },
                onScanFile: { data, isPDF in await viewModel.scanStatement(fileData: data, isPDF: isPDF) },
                onDelete: { await viewModel.deleteLoan(edit.loan) }
            )
        }
        .task { await viewModel.load() }
        .alert("Couldn't save", isPresented: .init(
            get: { viewModel.errorMessage != nil },
            set: { if !$0 { viewModel.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    private func row(_ loan: Components.Schemas.Account) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(loan.name).font(.body).foregroundStyle(.primary)
                HStack(spacing: 6) {
                    Text(loan._type.loanLabel)
                    if let payment = loan.minimumPayment, payment.amountMinor > 0 {
                        Text("· \(payment.formatted)/mo")
                    }
                    if let apr = loan.annualInterestRate, apr > 0 {
                        Text("· \(apr.formatted(.number.precision(.fractionLength(0...2))))% APR")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                if let matures = LoanDate.label(loan.maturityDate) {
                    let left = LoanDate.monthsLeft(loan.maturityDate)
                    Text("Matures \(matures)" + (left.map { " · \($0) payment\($0 == 1 ? "" : "s") left" } ?? ""))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                if loan._type == ._401kLoan {
                    Label("Payroll-deducted — not in safe-to-spend", systemImage: "building.columns")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Text(money(max(0, -loan.balance.amountMinor)))
                .font(.body.weight(.medium))
                .foregroundStyle(.primary)
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private func money(_ minor: Int64) -> String {
        Components.Schemas.Money(amountMinor: minor, currency: viewModel.currency).formatted
    }
}

/// Add or edit a loan. Amounts are entered in major units and converted to minor.
private struct LoanFormSheet: View {
    let existing: Components.Schemas.Account?
    let currency: String
    let onSave: (LoanDraft) async -> Bool
    let onScan: (UIImage) async -> Components.Schemas.LoanScanResult?
    let onScanFile: (Data, Bool) async -> Components.Schemas.LoanScanResult?
    let onDelete: (() async -> Void)?

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var type: Components.Schemas.AccountType
    @State private var balanceOwed: Double
    @State private var monthlyPayment: Double
    @State private var apr: Double
    @State private var hasMaturity: Bool
    @State private var maturityDate: Date
    /// M115: some statements state "N payments remaining" instead of an end
    /// date — either entry mode stores the same maturity date.
    @State private var endEntryMode: EndEntryMode = .date
    @State private var paymentsLeft: Int?

    enum EndEntryMode: String, CaseIterable {
        case date = "End date"
        case payments = "Payments left"
    }
    @State private var saving = false
    @State private var scanning = false
    @State private var showingCamera = false
    @State private var showingFileImporter = false
    @State private var scanNote: String?
    @State private var confirmingDelete = false

    init(
        existing: Components.Schemas.Account?,
        currency: String,
        onSave: @escaping (LoanDraft) async -> Bool,
        onScan: @escaping (UIImage) async -> Components.Schemas.LoanScanResult?,
        onScanFile: @escaping (Data, Bool) async -> Components.Schemas.LoanScanResult?,
        onDelete: (() async -> Void)?
    ) {
        self.existing = existing
        self.currency = currency
        self.onSave = onSave
        self.onScan = onScan
        self.onScanFile = onScanFile
        self.onDelete = onDelete
        _name = State(initialValue: existing?.name ?? "")
        _type = State(initialValue: existing?._type ?? .mortgage)
        _balanceOwed = State(initialValue: existing.map { Double(max(0, -$0.balance.amountMinor)) / 100 } ?? 0)
        _monthlyPayment = State(initialValue: existing?.minimumPayment.map { Double($0.amountMinor) / 100 } ?? 0)
        _apr = State(initialValue: existing?.annualInterestRate ?? 0)
        let maturity = LoanDate.date(from: existing?.maturityDate)
        _hasMaturity = State(initialValue: maturity != nil)
        _maturityDate = State(initialValue: maturity ?? Date())
    }

    private var canSave: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && !saving
    }

    var body: some View {
        NavigationStack {
            Form {
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
                            pasteStatement()
                        } label: {
                            Label("Paste from clipboard", systemImage: "doc.on.clipboard")
                        }
                    } label: {
                        if scanning {
                            HStack(spacing: 6) { ProgressView(); Text("Reading statement…") }
                        } else {
                            Label("Scan a statement", systemImage: "doc.viewfinder")
                        }
                    }
                    .disabled(scanning)
                    if let scanNote {
                        Text(scanNote).font(.caption).foregroundStyle(.secondary)
                    }
                } footer: {
                    Text("Photograph, upload, or paste your loan or lease statement (PDF or image) and the on-box vision model fills in what it can read. Confirm every value before saving.")
                }
                Section {
                    TextField("Name (e.g. 2022 Ascent — Subaru lease)", text: $name)
                    Picker("Type", selection: $type) {
                        ForEach(loanAccountTypes, id: \.self) { t in
                            Text(t.loanLabel).tag(t)
                        }
                    }
                } footer: {
                    if type == ._401kLoan {
                        Text("A 401(k) loan is borrowed from your own retirement, so it won't count against your net worth or total debt — it's a wash against your retirement balance. It's repaid by payroll deduction, so its payment is NOT counted against safe-to-spend either (that money never reaches your bank). Enter the real payment and balance anyway — they're only used to track payoff.")
                    }
                }
                Section("Balance you still owe") {
                    amountField("Amount owed (0 if paid off)", value: $balanceOwed)
                }
                Section("Monthly payment") {
                    amountField("Payment per month", value: $monthlyPayment)
                }
                Section {
                    Toggle("Has an end", isOn: $hasMaturity.animation())
                    if hasMaturity {
                        Picker("Enter as", selection: $endEntryMode.animation()) {
                            ForEach(EndEntryMode.allCases, id: \.self) { mode in
                                Text(mode.rawValue).tag(mode)
                            }
                        }
                        .pickerStyle(.segmented)
                        switch endEntryMode {
                        case .date:
                            DatePicker(
                                "Maturity date",
                                selection: $maturityDate,
                                displayedComponents: .date
                            )
                            if let left = LoanDate.monthsLeft(LoanDate.iso(from: maturityDate)) {
                                Text("\(left) payment\(left == 1 ? "" : "s") left")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        case .payments:
                            TextField(
                                "Number of payments remaining",
                                value: $paymentsLeft, format: .number
                            )
                            .keyboardType(.numberPad)
                            if let left = paymentsLeft, left > 0 {
                                Text(
                                    "ends around "
                                        + (LoanDate.label(
                                            LoanDate.iso(from: LoanDate.dateAfter(payments: left)))
                                            ?? "—")
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                    }
                } header: {
                    Text("Loan / lease end")
                } footer: {
                    Text("Enter the end date, or — when the statement only says how many payments remain — the number of monthly payments left. Either way drives the payoff view.")
                }
                Section {
                    HStack {
                        TextField("Interest rate", value: $apr, format: .number)
                            .keyboardType(.decimalPad)
                        Text("% APR").foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Interest rate (optional)")
                } footer: {
                    Text("Used for payoff estimates. Leave at 0 if you're not sure.")
                }
                if onDelete != nil {
                    Section {
                        Button("Delete this loan", role: .destructive) {
                            confirmingDelete = true
                        }
                    } footer: {
                        Text("Paid it off or added it by mistake? Deleting removes it from your debts and net worth.")
                    }
                }
            }
            .navigationTitle(existing == nil ? "Add a loan" : "Edit loan")
            .navigationBarTitleDisplayMode(.inline)
            .keyboardDoneButton()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }.disabled(!canSave)
                }
            }
            .confirmationDialog(
                "Delete this loan?", isPresented: $confirmingDelete, titleVisibility: .visible
            ) {
                Button("Delete", role: .destructive) {
                    Task {
                        await onDelete?()
                        dismiss()
                    }
                }
            }
            .onChange(of: endEntryMode) { _, mode in syncEndEntry(to: mode) }
            .fullScreenCover(isPresented: $showingCamera) {
                CameraPicker { image in handleScan { await onScan(image) } }
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
                handleScan { await onScanFile(data, isPDF) }
            }
        }
    }

    /// Switching entry mode carries the current value over, so flipping back and
    /// forth never loses what was entered (M115).
    private func syncEndEntry(to mode: EndEntryMode) {
        switch mode {
        case .payments:
            paymentsLeft = LoanDate.monthsLeft(LoanDate.iso(from: maturityDate)) ?? paymentsLeft
        case .date:
            if let left = paymentsLeft, left > 0 {
                maturityDate = LoanDate.dateAfter(payments: left)
            }
        }
    }

    /// Scan a statement straight off the clipboard (M114, ADR 0028) — a copied
    /// screenshot or PDF works exactly like a photographed/uploaded one.
    private func pasteStatement() {
        ClipboardImage.read { contents in
            switch contents {
            case .image(let image):
                handleScan { await onScan(image) }
            case .pdf(let data):
                handleScan { await onScanFile(data, true) }
            case .none:
                scanNote = "There's no image or PDF on your clipboard to paste."
            }
        }
    }

    /// Run a scan, show the spinner, and apply the result — never overwriting what
    /// the user already typed (their correction outranks the model's reading).
    private func handleScan(_ scan: @escaping () async -> Components.Schemas.LoanScanResult?) {
        scanning = true
        Task {
            let result = await scan()
            scanning = false
            guard let result else { return }
            if let payment = result.monthlyPaymentMinor { monthlyPayment = Double(payment) / 100 }
            if let balance = result.balanceMinor { balanceOwed = Double(balance) / 100 }
            if let rate = result.aprPercent { apr = rate }
            if let scanned = result.name, name.trimmingCharacters(in: .whitespaces).isEmpty {
                name = scanned
            }
            if let maturity = LoanDate.date(from: result.maturityDate) {
                maturityDate = maturity
                hasMaturity = true
                endEntryMode = .date  // the scan read a concrete date; show it
                paymentsLeft = LoanDate.monthsLeft(result.maturityDate)
            } else if let remaining = result.paymentsRemaining, remaining > 0 {
                // The statement stated "N payments remaining" but no end date —
                // preload it in that entry mode, exactly as printed (M115).
                paymentsLeft = remaining
                hasMaturity = true
                endEntryMode = .payments
            }
            if result.isLease == true, type == .mortgage { type = .autoLoan }
            scanNote = result.note
        }
    }

    private func amountField(_ label: String, value: Binding<Double>) -> some View {
        HStack {
            Text(currencySymbol).foregroundStyle(.secondary)
            TextField(label, value: value, format: .number.precision(.fractionLength(0...2)))
                .keyboardType(.decimalPad)
        }
    }

    /// The maturity the active entry mode implies (M115).
    private var effectiveMaturityDate: Date {
        endEntryMode == .payments && (paymentsLeft ?? 0) > 0
            ? LoanDate.dateAfter(payments: paymentsLeft ?? 0)
            : maturityDate
    }

    private var currencySymbol: String {
        Components.Schemas.Money(amountMinor: 0, currency: currency).formatted
            .filter { !$0.isNumber && $0 != "." && $0 != "," && !$0.isWhitespace }
            .first.map(String.init) ?? "$"
    }

    private func save() {
        saving = true
        let draft = LoanDraft(
            name: name.trimmingCharacters(in: .whitespaces),
            type: type,
            currency: currency,
            balanceOwedMinor: Self.minor(balanceOwed),
            monthlyPaymentMinor: Self.minor(monthlyPayment),
            aprPercent: apr > 0 ? apr : nil,
            maturityDate: hasMaturity ? LoanDate.iso(from: effectiveMaturityDate) : nil
        )
        Task {
            let ok = await onSave(draft)
            saving = false
            if ok { dismiss() }
        }
    }

    private static func minor(_ major: Double) -> Int64 {
        Int64((major * 100).rounded())
    }
}
