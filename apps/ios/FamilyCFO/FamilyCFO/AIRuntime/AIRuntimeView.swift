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
            if let banner = viewModel.applyBanner {
                Section {
                    Label(banner, systemImage: viewModel.isApplying ? "arrow.triangle.2.circlepath" : "info.circle")
                        .font(.callout)
                }
            }
            hardwareSection
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

    @ViewBuilder private var hardwareSection: some View {
        if let hardware = viewModel.hardware {
            Section {
                if let budget = viewModel.memoryBudgetGb {
                    LabeledContent("Memory for models", value: "\(Int(budget)) GB")
                }
                LabeledContent("Free disk", value: "\(Int(hardware.diskFreeGb)) GB")
            } header: {
                Text("This box")
            } footer: {
                Text("Fit verdicts below compare each model's estimated memory against this box.")
            }
        }
    }

    private var modelsSection: some View {
        Section {
            let rows = viewModel.searchResults ?? viewModel.models
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
                fitBadge(viewModel.fit(of: info), isCurrent: isCurrent)
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

    @ViewBuilder private func fitBadge(_ fit: AIRuntimeViewModel.Fit, isCurrent: Bool) -> some View {
        switch fit {
        case .fits:
            badge(isCurrent ? "Running" : "Fits this box", .green)
        case .tight:
            badge("Tight fit", .orange)
        case .tooBig:
            badge("Too big for this box", .red)
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
