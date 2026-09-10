import SwiftUI

/// Every budget on the wrist (ADR 0067 v10): the same server-owned summary and
/// per-envelope decisions the phone shows, read-only.
struct WatchBudgetsView: View {
    @Environment(WatchModel.self) private var model
    @State private var response: Components.Schemas.BudgetListResponse?
    @State private var errorMessage: String?
    @State private var generation: UInt64 = 0
    @State private var currentOwner: LoadOwner?
    @State private var committedOwner: LoadOwner?

    private struct LoadOwner: Equatable {
        let identity: String
        let revision: UInt64
        let generation: UInt64
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let response, belongsToCurrentSession(committedOwner) {
                    summary(response.summary)
                    Divider()
                    if response.budgets.isEmpty {
                        Text("No budgets yet — set them on the phone or web.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(response.budgets, id: \.id) { budget in
                            row(budget)
                        }
                    }
                } else if let errorMessage, belongsToCurrentSession(currentOwner) {
                    Text(errorMessage).font(.caption2).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle("Budgets")
        .task(id: model.sessionRevision) {
            await load(revision: model.sessionRevision, resetForSession: true)
        }
        .refreshable { await load(revision: model.sessionRevision) }
    }

    private func summary(_ summary: Components.Schemas.BudgetSummary) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Budgeted \(summary.totalBudgeted.formatted)")
                .font(.footnote.weight(.semibold))
            Text("\(summary.totalSpent.formatted) spent so far")
                .font(.caption2)
                .foregroundStyle(.secondary)
            if let note = summary.totalSpent.partialDisclosure {
                Text(note).font(.caption2).foregroundStyle(.secondary)
            }
            if let over = summary.overCount, let warning = summary.warningCount {
                Text(over > 0 ? "\(over) over budget" : warning > 0 ? "\(warning) near limit" : "On track")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Text("Budget health unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func row(_ budget: Components.Schemas.Budget) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(budget.categoryName).font(.footnote).lineLimit(1)
                Spacer()
                if let percent = budget.percentUsed,
                   budget.remaining != nil,
                   budget.status != nil
                {
                    Text("\(percent)%")
                        .font(.caption2)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                } else {
                    Text("Unavailable").font(.caption2).foregroundStyle(.secondary)
                }
            }
            if let percent = budget.percentUsed,
               budget.remaining != nil,
               budget.status != nil
            {
                let fraction = Double(percent) / 100
                ProgressView(value: min(fraction, 1))
                    .tint(fraction >= 1 ? .red : fraction >= 0.8 ? .orange : .green)
            }
            Text("\(budget.spent.formatted) of \(budget.limit.formatted)")
                .font(.caption2)
                .foregroundStyle(.secondary)
            if let note = budget.spent.partialDisclosure {
                Text(note).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func load(revision: UInt64, resetForSession: Bool = false) async {
        generation &+= 1
        if resetForSession { resetSettledState() }
        guard revision == model.sessionRevision,
            let identity = model.sessionIdentity, let client = model.client
        else {
            currentOwner = nil
            return
        }
        let owner = LoadOwner(
            identity: identity, revision: revision, generation: generation)
        currentOwner = owner
        do {
            let result = try await client.listBudgets(.init())
            guard owns(owner), !Task.isCancelled else { return }
            guard case .ok(let result) = result else {
                let refreshed = await model.requestFreshCredential(
                    expectedIdentity: owner.identity)
                if refreshed { return }  // the revision-keyed task restarts with the new credential
                guard owns(owner), !Task.isCancelled else { return }
                errorMessage = "The box answered unexpectedly."
                return
            }
            let loaded = try result.body.json
            guard owns(owner), !Task.isCancelled else { return }
            response = loaded
            committedOwner = owner
            errorMessage = nil
            guard owns(owner), !Task.isCancelled else { return }
            WatchFaceSnapshot.refreshBudgetCache(loaded)
        } catch {
            guard owns(owner), !Task.isCancelled else { return }
            errorMessage = "Can't reach the box."
        }
    }

    private func owns(_ owner: LoadOwner) -> Bool {
        currentOwner == owner
            && model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func belongsToCurrentSession(_ owner: LoadOwner?) -> Bool {
        guard let owner else { return false }
        return model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func resetSettledState() {
        currentOwner = nil
        committedOwner = nil
        response = nil
        errorMessage = nil
    }
}
