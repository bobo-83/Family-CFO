import Foundation
import Observation

/// Drives the AI Runtime screen: what the box is serving, the model catalog
/// with hardware-fit verdicts, search, and the swap flow with live progress.
@MainActor
@Observable
final class AIRuntimeViewModel {
    // Internal, not private: the model drill-down screen shares this API (and
    // this view model) so fit verdicts and the apply flow behave identically.
    let api: AIRuntimeAPI

    private(set) var status: Components.Schemas.AiRuntimeStatus?
    private(set) var models: [Components.Schemas.AiModelInfo] = []
    private(set) var hardware: Components.Schemas.AiHardwareProfile?
    /// ADR 0071: the raw runtime config — carries the cluster toggle.
    private(set) var config: Components.Schemas.AiRuntimeConfig?
    /// #181: per-household usage — operator telemetry; nil without the manage
    /// right (the endpoint 403s) or while the box hasn't answered yet.
    private(set) var usage: Components.Schemas.AiUsageResponse?
    private(set) var isLoading = false
    private(set) var searchResults: [Components.Schemas.AiModelInfo]?
    private(set) var isSearching = false
    var errorMessage: String?

    // The swap in flight (or just finished) — drives the progress banner.
    private(set) var applyState: Components.Schemas.AiSwapStatus?
    private(set) var isApplying = false

    init(api: AIRuntimeAPI) { self.api = api }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let statusTask = api.status()
            async let catalogTask = api.catalog()
            async let hardwareTask = api.hardware()
            async let configTask = api.runtimeConfig()
            async let usageTask = api.usage()
            status = try await statusTask
            models = try await catalogTask
            hardware = (try? await hardwareTask) ?? hardware
            config = (try? await configTask) ?? config
            // Telemetry, not a workflow: a 403 (no manage right) or any other
            // failure just leaves the usage section hidden (#181).
            usage = (try? await usageTask) ?? usage
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The memory the model must fit in: GPU when reported, else unified/
    /// system — minus what the resident vision describer already claims (#182).
    var memoryBudgetGb: Double? {
        guard let hw = hardware, let node = hw.gpuMemoryGb ?? hw.systemMemoryGb else {
            return nil
        }
        return node - (hw.visionReservedGb ?? 0)
    }

    enum Fit: Equatable {
        case fits
        case tight
        case tooBig
        case unknown
    }

    /// Mirrors the web page's verdict: comfortable under ~80% of the budget,
    /// tight up to 100%, too big beyond — unknown without a hardware profile.
    /// Models offered as cluster deployments (ADR 0071) are judged against
    /// the COMBINED budget.
    func fit(of model: Components.Schemas.AiModelInfo) -> Fit {
        let budgetGb = servesOnCluster(model) ? clusterMemoryGb : memoryBudgetGb
        guard let budget = budgetGb, budget > 0 else { return .unknown }
        let needed = model.estMemoryGb
        if needed <= budget * 0.8 { return .fits }
        if needed <= budget { return .tight }
        return .tooBig
    }

    // MARK: - Household usage (#181)

    /// A single-family box with the cap off needs no fairness view; two or
    /// more households always show it.
    var showsHouseholdUsage: Bool {
        guard let usage else { return false }
        return usage.households.count >= 2 || usage.chatHourlyLimit > 0
    }

    var fairUseCapLabel: String {
        guard let usage else { return "" }
        return usage.chatHourlyLimit > 0
            ? "Fair-use cap: \(usage.chatHourlyLimit) chats/hour per household"
            : "Fair-use cap: off"
    }

    /// "512 MB" / "1.4 GB" — decimal units, matching the web page.
    static func storageLabel(bytes: Int64) -> String {
        if bytes >= 1_000_000_000 {
            return String(format: "%.1f GB", Double(bytes) / 1_000_000_000)
        }
        return "\(Int((Double(bytes) / 1_000_000).rounded())) MB"
    }

    // MARK: - Two-box cluster (ADR 0071)

    var clusterPeerHost: String? { hardware?.clusterPeerHost }
    var clusterPeerReachable: Bool { hardware?.clusterPeerReachable ?? false }
    /// Combined budget across both boxes; nil while the peer is unreachable.
    var clusterMemoryGb: Double? { hardware?.clusterMemoryGb }
    var clusterEnabled: Bool { config?.clusterEnabled ?? false }
    /// Cluster models are offered only with the toggle ON and the peer reachable.
    var clusterActive: Bool { clusterEnabled && clusterPeerReachable }

    func isClusterModel(_ model: Components.Schemas.AiModelInfo) -> Bool {
        (model.minNodes ?? 1) >= 2
    }

    /// Needs the second box: either the catalog says so (min_nodes >= 2), or
    /// the model's estimated memory exceeds this box's own budget — the same
    /// budget the single-box fit verdict uses.
    func needsCluster(_ model: Components.Schemas.AiModelInfo) -> Bool {
        if isClusterModel(model) { return true }
        guard let budget = memoryBudgetGb, budget > 0 else { return false }
        return model.estMemoryGb > budget
    }

    /// True when the model is offered (and fit-judged) as a cluster deployment.
    func servesOnCluster(_ model: Components.Schemas.AiModelInfo) -> Bool {
        clusterActive && needsCluster(model)
    }

    /// The single-box rows. While the cluster is active, everything that needs
    /// both boxes moves to the cluster section; while it is inactive, only
    /// min_nodes >= 2 entries hide — too-big single-node models still show
    /// with their honest "Too big for this box" verdict.
    var singleNodeModels: [Components.Schemas.AiModelInfo] {
        (searchResults ?? models).filter { clusterActive ? !needsCluster($0) : !isClusterModel($0) }
    }

    /// Models that need both boxes, shown only while clustering is active.
    /// Search results flow through the same rule as the curated catalog.
    var clusterModels: [Components.Schemas.AiModelInfo] {
        guard clusterActive else { return [] }
        return (searchResults ?? models).filter { needsCluster($0) }
    }

    private(set) var isSavingCluster = false

    /// Flip cluster_enabled through the existing runtime-config PUT — the full
    /// object, preserving provider/base_url/model/enabled.
    func setClusterEnabled(_ on: Bool) async {
        guard let current = config, !isSavingCluster else { return }
        isSavingCluster = true
        defer { isSavingCluster = false }
        do {
            var updated = current
            updated.clusterEnabled = on
            config = try await api.updateRuntimeConfig(updated)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func runSearch(_ query: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            searchResults = nil
            return
        }
        isSearching = true
        defer { isSearching = false }
        do {
            searchResults = try await api.search(query: trimmed)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func clearSearch() {
        searchResults = nil
    }

    /// Apply a model (as the MAIN brain; the current vision model is kept —
    /// passing nil would disable photo analysis). Polls the swap to completion,
    /// then refreshes the runtime status.
    func apply(_ model: Components.Schemas.AiModelInfo) async {
        guard !isApplying else { return }
        isApplying = true
        defer { isApplying = false }
        do {
            let keepVision = status?.visionModel
            // ADR 0071: cluster-section models apply as a both-boxes deployment;
            // the server answers 409 (surfaced via errorMessage) if the peer is
            // down. Single-box applies send cluster: false.
            applyState = try await api.apply(
                mainModel: model.id, visionModel: keepVision, cluster: servesOnCluster(model))
            errorMessage = nil
            // Poll until the swap leaves `running` — downloads can take a while.
            while applyState?.state == .running {
                try await Task.sleep(for: .seconds(5))
                applyState = try await api.applyStatus()
            }
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    var applyBanner: String? {
        guard let applyState else { return nil }
        switch applyState.state {
        case .running:
            return "Swapping models — downloading and restarting the AI. This can take several minutes; you can leave this screen."
        case .succeeded:
            return "Model swap finished. The AI may take a few more minutes to finish loading."
        case .failed:
            let tail = (applyState.logTail ?? "").suffix(200)
            return "Model swap failed. \(tail)"
        default:
            return nil
        }
    }

    var statusLine: String {
        guard let status else { return "Checking…" }
        if status.ready, let served = status.servedModel {
            return "Answering with \(served)"
        }
        if let phase = status.loadingDetail, !phase.isEmpty {
            return "Loading — \(phase)"
        }
        return status.detail
    }
}
