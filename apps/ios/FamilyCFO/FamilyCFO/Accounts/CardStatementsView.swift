import SwiftUI
import UIKit

/// #11: the statement cycles recorded for one credit card — the exact amount
/// the card asks for and the day it's due, so "Due soon" stops guessing from a
/// running balance.
struct CardStatementsView: View {
    @Environment(AppModel.self) private var model
    @State var viewModel: CardStatementsViewModel
    @State private var adding = false

    /// ADR 0034: recording a statement changes an account, so it needs
    /// accounts.manage — the same right the server enforces. Without it the
    /// list is read-only: no add button, no row actions that would 403.
    private var canManage: Bool { model.rolePolicy.canManageAccounts }

    var body: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Section {
                    Label {
                        Text(verbatim: errorMessage)
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                    }
                    .font(.caption)
                    .foregroundStyle(.orange)
                }
            }
            if !viewModel.statements.isEmpty {
                Section {
                    ForEach(viewModel.statements) { statement in
                        row(statement)
                    }
                } header: {
                    Text("Recorded cycles")
                } footer: {
                    Text("A recorded cycle is the exact amount this card asks for. Without one, Due soon can only estimate from the running balance, which includes anything charged since the cycle closed.")
                }
            }
        }
        .navigationTitle("Statements")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if canManage {
                ToolbarItem(placement: .primaryAction) {
                    Button { adding = true } label: {
                        Label("Add statement", systemImage: "plus")
                    }
                }
            }
        }
        .overlay {
            if viewModel.isLoading && viewModel.statements.isEmpty {
                ProgressView()
            } else if viewModel.statements.isEmpty && !viewModel.isLoading {
                ContentUnavailableView(
                    "No statements yet",
                    systemImage: "doc.text",
                    description: Text("Add the closed cycle from your latest statement — or scan it — so this card's payment is an exact figure rather than an estimate."))
            }
        }
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .sheet(isPresented: $adding) {
            CardStatementSheet(
                currency: viewModel.currency,
                onScan: { image in await viewModel.scan(image) },
                onScanFile: { data, isPDF in await viewModel.scan(fileData: data, isPDF: isPDF) },
                onSave: { draft in await viewModel.record(draft) }
            )
        }
    }

    @ViewBuilder private func row(_ statement: Components.Schemas.CardStatement) -> some View {
        let isPaid = statement.paidAt != nil
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: statement.statementBalance.formattedExact)
                    .font(.body.weight(.medium))
                    .foregroundStyle(isPaid ? Color.secondary : Color.primary)
                Text(verbatim: CardStatementsViewModel.dueLine(statement))
                    .font(.caption)
                    .foregroundStyle(isPaid ? Color.green : Color.secondary)
                if let detail = CardStatementsViewModel.detailLine(statement) {
                    Text(verbatim: detail).font(.caption2).foregroundStyle(.tertiary)
                }
            }
            Spacer()
            if isPaid {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            }
        }
        .swipeActions(edge: .trailing) {
            if canManage {
                Button(role: .destructive) {
                    Task { await viewModel.delete(statement) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        }
        .swipeActions(edge: .leading) {
            if canManage {
                Button {
                    Task { await viewModel.setPaid(statement, paid: !isPaid) }
                } label: {
                    if isPaid {
                        Label("Clear paid", systemImage: "arrow.uturn.backward")
                    } else {
                        Label("Mark paid", systemImage: "checkmark.circle")
                    }
                }
                .tint(isPaid ? .orange : .green)
            }
        }
    }
}

/// Record one cycle: what the statement says is due, and when. The scan fills in
/// candidates — nothing is stored until Save.
private struct CardStatementSheet: View {
    let currency: String
    let onScan: (UIImage) async -> Components.Schemas.CardStatementScanResult?
    let onScanFile: (Data, Bool) async -> Components.Schemas.CardStatementScanResult?
    let onSave: (CardStatementsViewModel.Draft) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @State private var draft = CardStatementsViewModel.Draft()
    @State private var saving = false
    @State private var scanning = false
    @State private var scanNote: String?
    @State private var showingCamera = false
    @State private var showingFileImporter = false

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
                        Text(verbatim: scanNote).font(.caption).foregroundStyle(.secondary)
                    }
                } footer: {
                    Text("Photograph, upload, or paste your credit-card statement (PDF or image) and the on-box vision model fills in what it can read. Confirm every value before saving.")
                }
                Section {
                    amountField(String(localized: "Amount due"), value: $draft.amount)
                } header: {
                    Text("Statement balance")
                } footer: {
                    Text("The full amount this closed cycle asks for — not today's running balance.")
                }
                Section {
                    DatePicker(
                        "Due date",
                        selection: Binding(
                            get: { draft.dueDate },
                            // A date the user picked outranks anything a later
                            // scan reads.
                            set: { draft.dueDate = $0; draft.dueDateTouched = true }),
                        displayedComponents: .date)
                }
                Section {
                    Toggle("Minimum payment", isOn: $draft.hasMinimum)
                    if draft.hasMinimum {
                        amountField(String(localized: "Minimum due"), value: $draft.minimum)
                    }
                } footer: {
                    Text("Optional — the least this card will accept. Recorded for reference; Due soon still shows the full amount.")
                }
                Section {
                    Toggle("Statement period", isOn: $draft.hasPeriod)
                    if draft.hasPeriod {
                        DatePicker(
                            "Period start", selection: $draft.periodStart,
                            displayedComponents: .date)
                        DatePicker(
                            "Period end", selection: $draft.periodEnd, displayedComponents: .date)
                    }
                } footer: {
                    Text("Optional — the days this cycle covers.")
                }
            }
            .navigationTitle("Add statement")
            .navigationBarTitleDisplayMode(.inline)
            .keyboardDoneButton()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(!draft.canSave || saving)
                }
            }
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
        .presentationDetents([.medium, .large])
    }

    private func amountField(_ label: String, value: Binding<Double>) -> some View {
        HStack {
            Text(verbatim: currency).foregroundStyle(.secondary)
            TextField(label, value: value, format: .number.precision(.fractionLength(0...2)))
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
        }
    }

    private func save() {
        guard draft.canSave, !saving else { return }
        saving = true
        Task {
            let saved = await onSave(draft)
            saving = false
            if saved { dismiss() }
        }
    }

    private func pasteStatement() {
        ClipboardImage.read { contents in
            switch contents {
            case .image(let image):
                handleScan { await onScan(image) }
            case .pdf(let data):
                handleScan { await onScanFile(data, true) }
            case .none:
                scanNote = String(localized: "There's no image or PDF on your clipboard to paste.")
            }
        }
    }

    /// Run a scan and prefill — never overwriting what the user already typed
    /// (their correction outranks the model's reading), and never saving.
    private func handleScan(
        _ scan: @escaping () async -> Components.Schemas.CardStatementScanResult?
    ) {
        scanning = true
        Task {
            let result = await scan()
            scanning = false
            guard let result else { return }
            draft.apply(result)
            scanNote = result.note
        }
    }
}
