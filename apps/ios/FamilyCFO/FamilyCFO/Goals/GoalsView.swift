import SwiftUI

/// Financial goals on iOS (M119, ADR 0025 parity with the dashboard's Goals
/// page): each goal with its progress toward target, tap to edit (target,
/// date, priority, planned monthly contribution), swipe to delete, + to add.
struct GoalsView: View {
    @Bindable var viewModel: GoalsViewModel
    @State private var addingGoal = false
    @State private var editing: Components.Schemas.Goal?

    var body: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            if viewModel.goals.isEmpty && !viewModel.isLoading {
                Text("No goals yet. Add one with + — an emergency fund, a vacation, a new car.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(viewModel.goals, id: \.id) { goal in
                goalRow(goal)
                    .contentShape(Rectangle())
                    .onTapGesture { editing = goal }
                    .swipeActions(edge: .trailing) {
                        Button(role: .destructive) {
                            Task { await viewModel.delete(goal) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
            }
        }
        .navigationTitle("Goals")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { addingGoal = true } label: {
                    Label("Add goal", systemImage: "plus")
                }
            }
        }
        .sheet(isPresented: $addingGoal) {
            GoalFormSheet(existing: nil) { name, type, targetMinor, dueISO, priority, contribution in
                await viewModel.create(
                    .init(
                        name: name, _type: type,
                        target: .init(amountMinor: targetMinor, currency: "USD"),
                        targetDate: dueISO, priority: priority,
                        monthlyContribution: contribution.map {
                            .init(amountMinor: $0, currency: "USD")
                        }))
            }
        }
        .sheet(item: $editing) { goal in
            GoalFormSheet(existing: goal) { name, _, targetMinor, dueISO, priority, contribution in
                await viewModel.update(
                    id: goal.id,
                    .init(
                        name: name,
                        target: .init(amountMinor: targetMinor, currency: goal.target.currency),
                        targetDate: dueISO, priority: priority,
                        monthlyContribution: contribution.map {
                            .init(amountMinor: $0, currency: goal.target.currency)
                        }))
            }
        }
        .task { await viewModel.load() }
    }

    private func goalRow(_ goal: Components.Schemas.Goal) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(goal.name).lineLimit(1)
                Spacer()
                Text("\(goal.current.formatted) of \(goal.target.formatted)")
                    .font(.subheadline.weight(.medium))
                    .monospacedDigit()
            }
            if let progress = GoalsViewModel.progress(goal) {
                ProgressView(value: progress)
                    .tint(progress >= 1 ? .green : .accentColor)
            }
            HStack(spacing: 6) {
                Text(Self.typeLabel(goal._type))
                if let contribution = goal.monthlyContribution {
                    Text("· saving \(contribution.formattedExact)/mo")
                }
                if let due = goal.targetDate {
                    Text("· by \(BillsView.shortDate(due))")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    static func typeLabel(_ type: Components.Schemas.GoalType) -> String {
        switch type {
        case .emergencyFund: return "Emergency fund"
        case .vacation: return "Vacation"
        case .retirement: return "Retirement"
        case .college: return "College"
        case .vehicle: return "Vehicle"
        case .renovation: return "Renovation"
        case .other: return "Other"
        }
    }
}

/// Add or edit a goal — one form for both, per the uniform-experience rule.
/// The type is fixed once created (it drives progress semantics).
private struct GoalFormSheet: View {
    let existing: Components.Schemas.Goal?
    let onSave: (
        String, Components.Schemas.GoalType, Int64, String?, Int, Int64?
    ) async -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var type: Components.Schemas.GoalType
    @State private var target: Double?
    @State private var hasDate: Bool
    @State private var targetDate: Date
    @State private var priority: Int
    @State private var contribution: Double?

    init(
        existing: Components.Schemas.Goal?,
        onSave: @escaping (
            String, Components.Schemas.GoalType, Int64, String?, Int, Int64?
        ) async -> Void
    ) {
        self.existing = existing
        self.onSave = onSave
        _name = State(initialValue: existing?.name ?? "")
        _type = State(initialValue: existing?._type ?? .other)
        _target = State(initialValue: existing.map { Double($0.target.amountMinor) / 100 })
        let due = existing?.targetDate.flatMap { LoanDate.date(from: String($0.prefix(10))) }
        _hasDate = State(initialValue: due != nil)
        _targetDate = State(initialValue: due ?? Date())
        _priority = State(initialValue: existing?.priority ?? 3)
        _contribution = State(
            initialValue: existing?.monthlyContribution.map { Double($0.amountMinor) / 100 })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name (e.g. Hawaii 2027)", text: $name)
                    if existing == nil {
                        Picker("Type", selection: $type) {
                            ForEach(Components.Schemas.GoalType.allCases, id: \.self) { t in
                                Text(GoalsView.typeLabel(t)).tag(t)
                            }
                        }
                    }
                    TextField("Target amount", value: $target, format: .currency(code: "USD"))
                        .keyboardType(.decimalPad)
                    Picker("Priority", selection: $priority) {
                        ForEach(1...5, id: \.self) { p in
                            Text(p == 1 ? "1 — highest" : "\(p)").tag(p)
                        }
                    }
                }
                Section {
                    Toggle("Has a target date", isOn: $hasDate.animation())
                    if hasDate {
                        DatePicker("Target date", selection: $targetDate, displayedComponents: .date)
                    }
                }
                Section {
                    TextField(
                        "Monthly contribution", value: $contribution,
                        format: .currency(code: "USD")
                    )
                    .keyboardType(.decimalPad)
                } header: {
                    Text("Planned savings (optional)")
                } footer: {
                    Text("Declared monthly savings are reserved by the Overview's \"Left to spend this month\" — so spending money never quietly eats the plan.")
                }
            }
            .navigationTitle(existing == nil ? "Add goal" : "Edit goal")
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(existing == nil ? "Add" : "Save") { save() }
                        .disabled(
                            name.trimmingCharacters(in: .whitespaces).isEmpty
                                || (target ?? 0) <= 0)
                }
            }
        }
    }

    private func save() {
        let targetMinor = Int64(((target ?? 0) * 100).rounded())
        let contributionMinor: Int64? = contribution.flatMap {
            $0 > 0 ? Int64(($0 * 100).rounded()) : nil
        }
        let due = hasDate ? LoanDate.iso(from: targetDate) : nil
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        let chosenType = type
        let chosenPriority = priority
        dismiss()
        Task {
            await onSave(trimmed, chosenType, targetMinor, due, chosenPriority, contributionMinor)
        }
    }
}

/// `Goal` carries a stable `id`; conforming lets it drive `.sheet(item:)`.
extension Components.Schemas.Goal: Identifiable {}
