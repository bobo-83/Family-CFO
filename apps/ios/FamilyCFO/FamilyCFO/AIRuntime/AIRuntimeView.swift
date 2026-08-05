import SwiftUI

/// The AI Runtime screen (ADR 0025 parity with the web dashboard's page):
/// what the box is serving, a model catalog with does-it-fit verdicts against
/// the box's hardware, model search, and one-tap swaps with live progress.
struct AIRuntimeView: View {
    @Environment(AppModel.self) private var model
    // @State, NOT let: the Settings screen re-renders whenever AppModel
    // changes and rebuilds this destination with a FRESH view model — a plain
    // `let` adopts that never-loaded instance while `.task` (keyed on view
    // identity) never re-fires, leaving the screen stuck on "Checking…"
    // (user reports 2026-07-12 and 2026-07-22). @State keeps the first
    // instance alive across parent re-renders, like AdvisorKnowledgeView.
    @State var viewModel: AIRuntimeViewModel
    @State private var searchText = ""
    @State private var confirmingApply: Components.Schemas.AiModelInfo?

    private var canManage: Bool { model.rolePolicy.canManageAiRuntime }

    var body: some View {
        List {
            statusSection
            answerStatsSection
            usageSection
            if let banner = viewModel.applyBanner {
                Section {
                    Label(banner, systemImage: viewModel.isApplying ? "arrow.triangle.2.circlepath" : "info.circle")
                        .font(.callout)
                }
            }
            hardwareSection
            clusterModelsSection
            modelsSection
        }
        .navigationTitle("AI runtime")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .searchable(text: $searchText, prompt: "Search models (e.g. Qwen)")
        .onSubmit(of: .search) {
            Task { await viewModel.runSearch(searchText) }
        }
        .onChange(of: searchText) { _, newValue in
            if newValue.isEmpty { viewModel.clearSearch() }
        }
        .confirmationDialog(
            "Switch the AI model?",
            isPresented: Binding(
                get: { confirmingApply != nil },
                set: { if !$0 { confirmingApply = nil } }),
            titleVisibility: .visible,
            presenting: confirmingApply
        ) { candidate in
            Button("Switch to \(candidate.label)") {
                let target = candidate
                confirmingApply = nil
                Task { await viewModel.apply(target) }
            }
            Button("Cancel", role: .cancel) { confirmingApply = nil }
        } message: { candidate in
            Text(
                "Downloads \(candidate.label) if needed (~\(Int(candidate.estDiskGb)) GB) and restarts the AI. The advisor answers with deterministic snapshots until the new model finishes loading — usually a few minutes, longer on first download."
            )
        }
    }

    private var statusSection: some View {
        Section {
            HStack(spacing: 10) {
                Circle()
                    .fill(viewModel.status?.ready == true ? Color.green : Color.orange)
                    .frame(width: 10, height: 10)
                Text(viewModel.statusLine)
                    .font(.callout)
            }
            if let status = viewModel.status {
                LabeledContent("Model", value: status.model)
                if let vision = status.visionModel {
                    LabeledContent("Photo model", value: vision)
                }
            }
            if let error = viewModel.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        } header: {
            Text("Status")
        }
    }

    /// Median felt latency of advisor answers per model — evidence for picking.
    /// Present even while the runtime is off; hidden until there are samples.
    @ViewBuilder private var answerStatsSection: some View {
        if let stats = viewModel.status?.answerStats, !stats.isEmpty {
            Section {
                ForEach(stats, id: \.model) { stat in
                    HStack(alignment: .firstTextBaseline) {
                        Text(stat.model)
                            .font(.subheadline)
                            .lineLimit(1)
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(Self.answerTimeLabel(ms: stat.medianMs))
                                .font(.subheadline.weight(.semibold))
                            Text("\(stat.samples) answer\(stat.samples == 1 ? "" : "s")")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("Median answer time")
            } footer: {
                Text("Felt latency of recent advisor answers, per model.")
            }
        }
    }

    /// #181: per-household share of the box — the operator's fairness view.
    /// Hidden without the manage right (the endpoint 403s anyway) and for a
    /// single-family box with the fair-use cap off.
    @ViewBuilder private var usageSection: some View {
        if canManage, viewModel.showsHouseholdUsage, let usage = viewModel.usage {
            Section {
                ForEach(usage.households, id: \.householdId) { row in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.name)
                                .font(.subheadline)
                                .lineLimit(1)
                            Spacer()
                            Text(AIRuntimeViewModel.storageLabel(bytes: row.storageBytes))
                                .font(.subheadline.weight(.semibold))
                        }
                        Text("\(row.chats7d) chat\(row.chats7d == 1 ? "" : "s") this week (\(row.chats24h) in the last 24 h)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        if let median = row.medianAnswerMs {
                            Text("\(Self.answerTimeLabel(ms: median)) median answer")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        // #196: members present but no member key yet — can't be
                        // sealed. Informational, so amber rather than an error red.
                        if row.memberKeyOk == false {
                            Text("member sign-in needed")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                    }
                }
            } header: {
                Text("Household usage")
            } footer: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(viewModel.fairUseCapLabel)
                    if viewModel.anyHouseholdNeedsMemberSignIn {
                        Text("Households marked \"member sign-in needed\" can't be sealed until a member signs in with their password.")
                    }
                }
            }
        }
    }

    /// "8.3s" under a minute, "1m 24s" above.
    static func answerTimeLabel(ms: Int) -> String {
        let seconds = Double(ms) / 1000
        if seconds < 60 { return String(format: "%.1fs", seconds) }
        let total = Int(seconds.rounded())
        let rest = total % 60
        return rest == 0 ? "\(total / 60)m" : "\(total / 60)m \(rest)s"
    }

    @ViewBuilder private var hardwareSection: some View {
        if let hardware = viewModel.hardware {
            Section {
                if let budget = viewModel.memoryBudgetGb {
                    LabeledContent("Memory for models", value: "\(Int(budget)) GB")
                }
                LabeledContent("Free disk", value: "\(Int(hardware.diskFreeGb)) GB")
                clusterRows
            } header: {
                Text("This box")
            } footer: {
                Text("Fit verdicts below compare each model's estimated memory against this box.")
            }
        }
    }

    /// ADR 0071: the enrolled second box — detection line + the cluster toggle.
    @ViewBuilder private var clusterRows: some View {
        if let peer = viewModel.clusterPeerHost {
            if viewModel.clusterPeerReachable {
                if let combined = viewModel.clusterMemoryGb {
                    Label(
                        "Second box detected (\(peer)) — combined budget \(Int(combined)) GB",
                        systemImage: "server.rack"
                    )
                    .font(.callout)
                } else {
                    Label("Second box detected (\(peer))", systemImage: "server.rack")
                        .font(.callout)
                }
            } else {
                Label(
                    "Second box (\(peer)) enrolled but not reachable",
                    systemImage: "exclamationmark.triangle"
                )
                .font(.callout)
                .foregroundStyle(.orange)
            }
            Toggle(
                "Use both boxes for larger models",
                isOn: Binding(
                    get: { viewModel.clusterEnabled },
                    set: { on in Task { await viewModel.setClusterEnabled(on) } })
            )
            .disabled(!viewModel.clusterPeerReachable || viewModel.isSavingCluster || !canManage)
        }
    }

    /// ADR 0071: cluster-tier models — only with the toggle on + peer reachable.
    @ViewBuilder private var clusterModelsSection: some View {
        if !viewModel.clusterModels.isEmpty {
            Section {
                ForEach(viewModel.clusterModels, id: \.id) { info in
                    modelRow(info)
                }
            } header: {
                Text("Cluster (both boxes)")
            } footer: {
                if let combined = viewModel.clusterMemoryGb {
                    Text("These models run split across both boxes — fit is against the combined \(Int(combined)) GB budget.")
                }
            }
        }
    }

    private var modelsSection: some View {
        Section {
            let rows = viewModel.singleNodeModels
            if viewModel.isSearching {
                HStack { ProgressView(); Text("Searching…").padding(.leading, 8) }
            }
            ForEach(rows, id: \.id) { info in
                modelRow(info)
            }
            if rows.isEmpty && !viewModel.isLoading && !viewModel.isSearching {
                Text("No models found.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text(viewModel.searchResults == nil ? "Curated models" : "Search results")
        } footer: {
            if !canManage {
                Text("Only a member with AI-runtime management rights can switch models.")
            }
        }
    }

    @ViewBuilder private func modelRow(_ info: Components.Schemas.AiModelInfo) -> some View {
        let isCurrent = info.id == viewModel.status?.model
        // The row is a drill-down (user request 2026-07-22); the trailing Use
        // button keeps the one-tap swap for people who already know the model.
        NavigationLink {
            AIRuntimeModelDetailView(info: info, runtime: viewModel)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(info.label).font(.subheadline).lineLimit(1)
                    HStack(spacing: 6) {
                        Text("\(Self.format(info.parametersB))B · ~\(Int(info.estMemoryGb)) GB")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        if info.supportsVision {
                            Text("vision").font(.caption2).foregroundStyle(.blue)
                        }
                        if info.gated {
                            Text("gated").font(.caption2).foregroundStyle(.orange)
                        }
                    }
                    fitBadge(
                        viewModel.fit(of: info),
                        isCurrent: isCurrent,
                        cluster: viewModel.servesOnCluster(info))
                }
                Spacer()
                if isCurrent {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                } else if canManage && viewModel.fit(of: info) != .tooBig {
                    Button("Use") { confirmingApply = info }
                        .buttonStyle(.bordered)
                        .disabled(viewModel.isApplying)
                }
            }
        }
    }

    @ViewBuilder private func fitBadge(
        _ fit: AIRuntimeViewModel.Fit, isCurrent: Bool, cluster: Bool = false
    ) -> some View {
        switch fit {
        case .fits:
            badge(isCurrent ? "Running" : (cluster ? "Fits across both boxes" : "Fits this box"), .green)
        case .tight:
            badge("Tight fit", .orange)
        case .tooBig:
            badge(cluster ? "Too big even for both boxes" : "Too big for this box", .red)
        case .unknown:
            EmptyView()
        }
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private static func format(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(format: "%.1f", value)
    }
}
