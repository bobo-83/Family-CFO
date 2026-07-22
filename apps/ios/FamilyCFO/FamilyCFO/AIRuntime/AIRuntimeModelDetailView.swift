import SwiftUI

/// Drill-down for one model (user request 2026-07-22): everything the catalog
/// row knows, plus the Hugging Face hub's live stats — enough to decide on a
/// swap without leaving the app. Shares the list screen's view model so the
/// fit verdict, apply flow, and progress banner behave identically.
struct AIRuntimeModelDetailView: View {
    @Environment(AppModel.self) private var model
    let info: Components.Schemas.AiModelInfo
    let runtime: AIRuntimeViewModel
    @State private var detail: Components.Schemas.AiModelDetail?
    @State private var isLoadingDetail = true
    @State private var confirmingApply = false

    private var canManage: Bool { model.rolePolicy.canManageAiRuntime }
    private var isCurrent: Bool { info.id == runtime.status?.model }

    var body: some View {
        List {
            aboutSection
            fitSection
            hubSection
            if let notes = info.notes, !notes.isEmpty {
                Section("Notes") {
                    Text(notes).font(.callout)
                }
            }
            actionSection
        }
        .navigationTitle(info.label)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            defer { isLoadingDetail = false }
            detail = try? await runtime.api.detail(id: info.id)
        }
        .confirmationDialog(
            "Switch the AI model?",
            isPresented: $confirmingApply,
            titleVisibility: .visible
        ) {
            Button("Switch to \(info.label)") {
                confirmingApply = false
                Task { await runtime.apply(info) }
            }
            Button("Cancel", role: .cancel) { confirmingApply = false }
        } message: {
            Text(
                "Downloads \(info.label) if needed (~\(Int(info.estDiskGb)) GB) and restarts the AI. The advisor answers with deterministic snapshots until the new model finishes loading — usually a few minutes, longer on first download."
            )
        }
    }

    private var aboutSection: some View {
        Section("Model") {
            LabeledContent("Repository") {
                Text(info.id).font(.caption.monospaced()).textSelection(.enabled)
            }
            LabeledContent("Parameters", value: "\(Self.format(info.parametersB))B")
            LabeledContent("Estimated memory", value: "~\(Int(info.estMemoryGb)) GB")
            LabeledContent("Download size", value: "~\(Int(info.estDiskGb)) GB")
            LabeledContent("Role", value: roleLabel)
            if let parser = info.toolParser {
                LabeledContent("Tool parser", value: parser)
            }
            if info.supportsVision {
                Label("Understands photos", systemImage: "photo")
                    .font(.callout)
            }
            if info.gated {
                Label("Gated — needs a Hugging Face license agreement", systemImage: "lock")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder private var fitSection: some View {
        Section("Fit on this box") {
            switch runtime.fit(of: info) {
            case .fits:
                Label(isCurrent ? "Running now" : "Fits this box", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
            case .tight:
                Label("Tight fit — it will load, with little headroom", systemImage: "exclamationmark.circle")
                    .foregroundStyle(.orange)
            case .tooBig:
                Label("Too big for this box", systemImage: "xmark.circle")
                    .foregroundStyle(.red)
            case .unknown:
                Text("Hardware profile unavailable — no fit verdict.")
                    .foregroundStyle(.secondary)
            }
            if let budget = runtime.memoryBudgetGb {
                LabeledContent("Memory for models", value: "\(Int(budget)) GB")
            }
        }
    }

    @ViewBuilder private var hubSection: some View {
        Section("On Hugging Face") {
            if let detail {
                if let downloads = detail.downloads {
                    LabeledContent("Downloads", value: downloads.formatted())
                }
                if let likes = detail.likes {
                    LabeledContent("Likes", value: likes.formatted())
                }
                if let modified = detail.lastModified {
                    LabeledContent("Updated", value: Self.formatDate(modified))
                }
                if let license = detail.license {
                    LabeledContent("License", value: license)
                }
                if let tags = detail.tags, !tags.isEmpty {
                    Text(tags.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else if isLoadingDetail {
                HStack { ProgressView(); Text("Fetching hub stats…").padding(.leading, 8) }
                    .font(.callout)
            } else {
                Text("Hub stats unavailable (offline or unknown model).")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private var actionSection: some View {
        if isCurrent {
            Section {
                Label("This model is answering right now", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            }
        } else if canManage {
            Section {
                Button {
                    confirmingApply = true
                } label: {
                    Label("Use this model", systemImage: "arrow.triangle.2.circlepath")
                }
                .disabled(runtime.isApplying || runtime.fit(of: info) == .tooBig)
                if let banner = runtime.applyBanner {
                    Text(banner).font(.caption).foregroundStyle(.secondary)
                }
            } footer: {
                if runtime.fit(of: info) == .tooBig {
                    Text("This model's estimated memory exceeds the box — switching is disabled.")
                }
            }
        }
    }

    private var roleLabel: String {
        switch info.role {
        case .main: return "Advisor (text)"
        case .vision: return "Photo describer"
        case .both: return "Advisor + photos"
        }
    }

    private static func format(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(format: "%.1f", value)
    }

    private static func formatDate(_ iso: String) -> String {
        guard let date = ISO8601DateFormatter.withFractionalSeconds.date(from: iso)
            ?? ISO8601DateFormatter().date(from: iso)
        else { return iso }
        return date.formatted(date: .abbreviated, time: .omitted)
    }
}

extension ISO8601DateFormatter {
    fileprivate static let withFractionalSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
