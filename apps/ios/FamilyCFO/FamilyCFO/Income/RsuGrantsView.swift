import SwiftUI

/// The RSU grants surface of the Income tab (M-rsu-grants): each grant with its
/// vest schedule, the cached live quote with a manual refresh, and the derived
/// next-12-months value that replaces the flat RSU annual figure.

/// `RsuVestEvent` carries a stable id, so it can drive `.sheet(item:)`.
extension Components.Schemas.RsuVestEvent: Identifiable {}

/// The "RSU grants" section of the Income list: quote header, derived annual,
/// one row per grant (tap for the vest schedule, swipe to delete), and the
/// add-grant on-ramp.
struct RsuGrantsSection: View {
    let viewModel: IncomeViewModel
    @State private var addingGrant = false

    var body: some View {
        Section {
            if let rsu = viewModel.rsuGrants {
                ForEach(rsu.quotes, id: \.ticker) { quote in
                    quoteRow(quote)
                }
                if let annual = rsu.derivedAnnual {
                    Text("Next 12 months at live price: \(annual.formattedExact)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(rsu.grants, id: \.id) { grant in
                    NavigationLink {
                        RsuVestScheduleView(viewModel: viewModel, grantID: grant.id)
                    } label: {
                        grantRow(grant)
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task { await viewModel.deleteGrant(grant) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
            }
            Button {
                addingGrant = true
            } label: {
                Label("Add RSU grant", systemImage: "plus.circle")
            }
        } header: {
            Text("RSU grants")
        } footer: {
            Text("Tap a grant to see and edit its vest schedule. Swipe a grant to delete it.")
        }
        .sheet(isPresented: $addingGrant) {
            RsuGrantFormView(viewModel: viewModel)
        }
    }

    /// "XYZ · $3,500.00" with the quote's as-of date and a manual refresh.
    private func quoteRow(_ quote: Components.Schemas.StockQuote) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: "\(quote.ticker) · \(quote.price.formattedExact)")
                    .font(.subheadline.weight(.medium))
                Text("as of \(quote.asOf.formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await viewModel.refreshQuote() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("Refresh quote")
        }
    }

    /// "800 XYZ · vests quarterly over 2 years" with the grant date below.
    private func grantRow(_ grant: Components.Schemas.RsuGrant) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(
                verbatim: String(
                    localized:
                        "\(grant.units) \(grant.ticker) · vests \(grant.frequency.rawValue) over \(grant.vestYears) years"
                ))
            Text("Granted \(String(grant.grantDate.prefix(10)))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

/// A grant's vest schedule: every tranche with its date, units, and value at
/// the live quote. Tap a tranche to edit it, swipe to remove it, + to add one.
struct RsuVestScheduleView: View {
    let viewModel: IncomeViewModel
    let grantID: String
    @State private var editingEvent: Components.Schemas.RsuVestEvent?
    @State private var addingEvent = false

    /// Always read the grant out of the view model, so edits re-render here.
    private var grant: Components.Schemas.RsuGrant? {
        viewModel.rsuGrants?.grants.first { $0.id == grantID }
    }

    var body: some View {
        List {
            if let grant {
                Section {
                    ForEach(grant.events, id: \.id) { event in
                        eventRow(event)
                            .contentShape(Rectangle())
                            .onTapGesture { editingEvent = event }
                            .swipeActions {
                                Button(role: .destructive) {
                                    Task { await viewModel.deleteVestEvent(id: event.id) }
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                    }
                } footer: {
                    Text("Tap a vest to edit it — e.g. after a refresh grant changes a tranche. Swipe to remove one.")
                }
            }
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .navigationTitle(
            grant.map { "\($0.units) \($0.ticker)" } ?? String(localized: "Vest schedule"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    addingEvent = true
                } label: {
                    Label("Add vest", systemImage: "plus")
                }
            }
        }
        .sheet(item: $editingEvent) { event in
            RsuVestEventFormView(viewModel: viewModel, grantID: grantID, mode: .edit(event))
        }
        .sheet(isPresented: $addingEvent) {
            RsuVestEventFormView(viewModel: viewModel, grantID: grantID, mode: .add)
        }
    }

    private func eventRow(_ event: Components.Schemas.RsuVestEvent) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: Self.vestDateText(event.vestDate))
                Text("\(event.units) units")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let value = event.value {
                Text(verbatim: value.formattedExact)
                    .font(.subheadline.weight(.medium))
            }
        }
    }

    /// "2027-02-15" → "Feb 15, 2027" (a schedule spans years, so keep the year).
    static func vestDateText(_ iso: String) -> String {
        guard let date = BillFormView.parseDate(iso) else { return iso }
        return date.formatted(.dateTime.year().month(.abbreviated).day())
    }
}

/// Add a grant by hand: whose it is, the ticker, total units, grant date, and
/// the vesting shape. The server derives the tranche schedule from these.
struct RsuGrantFormView: View {
    @Environment(\.dismiss) private var dismiss
    let viewModel: IncomeViewModel

    @State private var earnerID = ""
    @State private var ticker = ""
    @State private var units: Int?
    @State private var grantDate = Date()
    @State private var vestYears = 2
    @State private var frequency: Components.Schemas.RsuGrantCreateRequest.FrequencyPayload =
        .quarterly

    private var trimmedTicker: String {
        ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Earner", selection: $earnerID) {
                        Text("Choose…").tag("")
                        ForEach(viewModel.earners, id: \.id) { earner in
                            Text(verbatim: earner.label).tag(earner.id)
                        }
                    }
                    TextField("Ticker (e.g. XYZ)", text: $ticker)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                    TextField("Units", value: $units, format: .number)
                        .keyboardType(.numberPad)
                    DatePicker("Grant date", selection: $grantDate, displayedComponents: .date)
                    Picker("Vests over", selection: $vestYears) {
                        ForEach(1...10, id: \.self) { years in
                            Text("\(years) years").tag(years)
                        }
                    }
                    Picker("Frequency", selection: $frequency) {
                        ForEach(
                            Components.Schemas.RsuGrantCreateRequest.FrequencyPayload.allCases,
                            id: \.self
                        ) { f in
                            Text(f.rawValue.capitalized).tag(f)
                        }
                    }
                } footer: {
                    Text("The vest schedule is derived from these — you can adjust individual tranches afterward.")
                }
            }
            .navigationTitle("Add RSU grant")
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { save() }
                        .disabled(
                            earnerID.isEmpty || trimmedTicker.isEmpty || (units ?? 0) <= 0)
                }
            }
        }
    }

    private func save() {
        guard let units, units > 0 else { return }
        let date = BillFormView.isoDate(grantDate)
        let symbol = trimmedTicker
        dismiss()
        Task {
            await viewModel.addGrant(
                earnerID: earnerID,
                ticker: symbol,
                units: units,
                grantDate: date,
                vestYears: vestYears,
                frequency: frequency
            )
        }
    }
}

/// Add or edit one vest tranche: its date and units. One form for both flows
/// (the "uniform experience" rule) — the mode only changes the title, the
/// button, and whether fields start blank or pre-filled.
struct RsuVestEventFormView: View {
    enum Mode {
        case add
        case edit(Components.Schemas.RsuVestEvent)
    }

    @Environment(\.dismiss) private var dismiss
    let viewModel: IncomeViewModel
    let grantID: String
    let mode: Mode

    @State private var date: Date
    @State private var units: Int?

    init(viewModel: IncomeViewModel, grantID: String, mode: Mode) {
        self.viewModel = viewModel
        self.grantID = grantID
        self.mode = mode
        switch mode {
        case .add:
            _date = State(initialValue: Date())
            _units = State(initialValue: nil)
        case .edit(let event):
            _date = State(initialValue: BillFormView.parseDate(event.vestDate) ?? Date())
            _units = State(initialValue: event.units)
        }
    }

    private var isEditing: Bool {
        if case .edit = mode { return true }
        return false
    }

    var body: some View {
        NavigationStack {
            Form {
                DatePicker("Vest date", selection: $date, displayedComponents: .date)
                TextField("Units", value: $units, format: .number)
                    .keyboardType(.numberPad)
            }
            .navigationTitle(isEditing ? "Edit vest" : "Add vest")
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isEditing ? "Save" : "Add") { save() }
                        .disabled((units ?? 0) <= 0)
                }
            }
        }
    }

    private func save() {
        guard let units, units > 0 else { return }
        let iso = BillFormView.isoDate(date)
        dismiss()
        Task {
            switch mode {
            case .add:
                await viewModel.addVestEvent(grantID: grantID, date: iso, units: units)
            case .edit(let event):
                await viewModel.updateVestEvent(id: event.id, date: iso, units: units)
            }
        }
    }
}
