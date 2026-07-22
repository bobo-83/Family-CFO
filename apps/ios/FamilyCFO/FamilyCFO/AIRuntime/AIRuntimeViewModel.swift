import Foundation
import Observation

/// Drives the AI Runtime screen: what the box is serving, the model catalog
/// with hardware-fit verdicts, search, and the swap flow with live progress.
@MainActor
@Observable
final class AIRuntimeViewModel {
    private let api: AIRuntimeAPI

    private(set) var status: Components.Schemas.AiRuntimeStatus?
    private(set) var models: [Components.Schemas.AiModelInfo] = []
    private(set) var hardware: Components.Schemas.AiHardwareProfile?
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
            status = try await statusTask
            models = try await catalogTask
            hardware = (try? await hardwareTask) ?? hardware
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The memory the model must fit in: GPU when reported, else unified/system.
    var memoryBudgetGb: Double? {
        hardware.flatMap { $0.gpuMemoryGb ?? $0.systemMemoryGb }
    }

    enum Fit: Equatable {
        case fits
        case tight
        case tooBig
        case unknown
    }

    /// Mirrors the web page's verdict: comfortable under ~80% of the budget,
    /// tight up to 100%, too big beyond — unknown without a hardware profile.
    func fit(of model: Components.Schemas.AiModelInfo) -> Fit {
        guard let budget = memoryBudgetGb, budget > 0 else { return .unknown }
        let needed = model.estMemoryGb
        if needed <= budget * 0.8 { return .fits }
        if needed <= budget { return .tight }
        return .tooBig
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
            applyState = try await api.apply(mainModel: model.id, visionModel: keepVision)
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
