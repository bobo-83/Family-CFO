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

    // MARK: - Apply: one request/observation lane (#170, ADR 0081)

    /// The apply job being observed — the server's state is authoritative.
    /// Success is never inferred from serving identity: the client follows
    /// job ID + sequence + lifecycle state to a terminal state.
    private(set) var applyJob: Components.Schemas.AiApplyJobStatus?
    private(set) var isApplying = false
    private(set) var isRetryingRecovery = false
    /// Transient poll failure: the operation may still be running (#170).
    private(set) var progressUnavailable = false

    /// The observation loop is a view-model-owned task, independent of the
    /// button task that started the apply: attach on presentation, detach on
    /// dismissal, and a stale session's completions never update this one.
    private var observationTask: Task<Void, Never>?
    private var observedJobID: UUID?
    private var observationGeneration = 0
    private var lastSequence = 0
    /// Tests inject .zero for deterministic polling.
    var pollInterval: Duration = .seconds(5)

    /// Terminal per ADR 0081; commit_repair_required is terminal in outcome
    /// but stays forward-repairable.
    private static let terminalStates: Set<Components.Schemas.AiApplyJobStatus.LifecycleStatePayload> = [
        .succeeded, .failed, .restored, .recoveryFailed, .commitRepairRequired,
    ]

    private static func isTerminal(
        _ lifecycle: Components.Schemas.AiApplyJobStatus.LifecycleStatePayload?
    ) -> Bool {
        guard let lifecycle else { return false }
        return terminalStates.contains(lifecycle)
    }

    /// A replacement is unfinished until a terminal state: new applies are
    /// blocked and the server would 409 racing managed-config PUTs anyway.
    var replacementInFlight: Bool {
        if isApplying { return true }
        return !Self.isTerminal(applyJob?.lifecycleState) && applyJob?.lifecycleState != nil
    }

    /// The user-facing phase of the observed job (#170): validation, loading,
    /// active, failed, restored, and recovery failed stay distinct (issue
    /// #170's required distinction).
    enum ApplyPhase {
        case preflight
        case preparing
        case replacing
        case commitPending
        case succeeded
        case failed
        case restoring
        case restored
        case recovery

        var isFailure: Bool { self == .failed || self == .recovery }
    }

    var applyPhase: ApplyPhase? {
        if isApplying { return .preflight }
        switch applyJob?.lifecycleState {
        case .accepted, .staging: return .preparing
        case .launching, .verifying: return .replacing
        case .commitPending: return .commitPending
        case .restoring: return .restoring
        case .succeeded: return .succeeded
        case .failed: return .failed
        case .restored: return .restored
        case .recoveryFailed, .commitRepairRequired: return .recovery
        default: return nil  // the explicit idle/unavailable read
        }
    }

    /// Safe cause for a failed/recovery job: the redacted message, else the code.
    var applyFailureDetail: String? {
        guard let job = applyJob else { return nil }
        return job.message ?? job.reasonCode ?? job.logTail
    }

    /// `recovery_failed` / `commit_repair_required` offer the frozen retry.
    var recoveryAvailable: Bool { applyJob?.recoveryAvailable ?? false }

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
            ? String(localized: "Fair-use cap: \(usage.chatHourlyLimit) chats/hour per household")
            : String(localized: "Fair-use cap: off")
    }

    /// #196: any household with members but no member key yet — surfaces the footnote.
    var anyHouseholdNeedsMemberSignIn: Bool {
        usage?.households.contains { $0.memberKeyOk == false } ?? false
    }

    /// "512 MB" / "1.4 GB" — decimal units, matching the web page.
    static func storageLabel(bytes: Int64) -> String {
        if bytes >= 1_000_000_000 {
            let gigabytes = String(format: "%.1f", Double(bytes) / 1_000_000_000)
            return String(localized: "\(gigabytes) GB")
        }
        return String(localized: "\(Int((Double(bytes) / 1_000_000).rounded())) MB")
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

    /// The role intent for applying `model` (#170): a vision-only model pairs
    /// with the current main; a dual-capable main serves photos itself; a
    /// text-only main preserves the explicit current photo mode. Topology
    /// stays 'auto' — the server resolves single vs two-node serving from the
    /// household's consent and the plan (cluster placement here is advisory).
    func applyRequest(for model: Components.Schemas.AiModelInfo)
        -> Components.Schemas.AiApplyRequest?
    {
        let currentMain = status?.servedModel ?? config?.model
        if model.role == .vision {
            // An empty configured main is "none" — refuse locally rather than
            // POST a mainModel of "".
            guard let currentMain, !currentMain.isEmpty else { return nil }
            if currentMain == model.id {
                // Same model for both roles = ONE instance, never two copies.
                return .init(mainModel: model.id, photoMode: .main, topology: .auto)
            }
            return .init(
                mainModel: currentMain, visionModel: model.id,
                photoMode: .separate, topology: .auto)
        }
        if model.supportsVision {
            return .init(mainModel: model.id, photoMode: .main, topology: .auto)
        }
        // Text-only main: keep the served vision model — or photos off, explicitly.
        if let keptVision = status?.visionModel {
            return .init(
                mainModel: model.id, visionModel: keptVision,
                photoMode: .separate, topology: .auto)
        }
        return .init(mainModel: model.id, photoMode: .off, topology: .auto)
    }

    /// Apply a model after the user's confirmation (#170). Builds the role
    /// intent, POSTs once with a fresh request_id, and hands the returned job
    /// to the view-model-owned observation task. The initiating button task
    /// ENDS here — observation outlives it.
    func apply(_ model: Components.Schemas.AiModelInfo) async {
        guard !isApplying, !replacementInFlight else { return }
        guard var request = applyRequest(for: model) else {
            errorMessage = String(
                localized: "No chat model is configured to pair with a photo model.")
            return
        }
        isApplying = true
        defer { isApplying = false }
        // One request_id per user confirmation, retained across
        // transport-ambiguous retries — never a second launch (§6).
        request.requestId = UUID().uuidString
        do {
            let job = try await api.apply(request: request)
            errorMessage = nil
            beginObservation(job)
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Retry recovery: re-run the job's frozen decision direction — never a
    /// new model. A NEW request_id per attempt; the returned job re-observed.
    func retryRecovery() async {
        guard let jobID = applyJob?.jobId.flatMap(UUID.init(uuidString:)),
            !isRetryingRecovery, !isApplying
        else { return }
        isRetryingRecovery = true
        defer { isRetryingRecovery = false }
        do {
            let job = try await api.retryRecovery(jobID: jobID, requestID: UUID())
            errorMessage = nil
            beginObservation(job)
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Anchor observation to a job: any earlier loop is superseded (#170).
    private func beginObservation(_ job: Components.Schemas.AiApplyJobStatus) {
        guard let jobID = job.jobId.flatMap(UUID.init(uuidString:)) else { return }
        observationGeneration += 1
        let generation = observationGeneration
        observedJobID = jobID
        lastSequence = job.sequence ?? 0
        applyJob = job
        progressUnavailable = false
        observationTask?.cancel()
        observationTask = Task { [weak self] in
            await self?.observe(jobID: jobID, generation: generation)
        }
    }

    /// The observation loop: poll the exact job, accept only increasing
    /// sequence numbers, stop at a terminal state. Cancellation is observation
    /// teardown — never a replacement failure (#170).
    private func observe(jobID: UUID, generation: Int) async {
        while !Task.isCancelled {
            do {
                try await Task.sleep(for: pollInterval)
                guard !Task.isCancelled else { return }
                let job = try await api.applyStatus(jobID: jobID)
                // A stale session (dismissed and re-presented, or a newer
                // observation) must never commit into this one.
                guard generation == observationGeneration, !Task.isCancelled else { return }
                // Accept only the observed job's own, strictly increasing
                // sequence numbers — out-of-order completions never move the
                // state backwards.
                guard job.jobId.flatMap(UUID.init(uuidString:)) == jobID else { continue }
                guard (job.sequence ?? 0) > lastSequence else { continue }
                lastSequence = job.sequence ?? 0
                applyJob = job
                progressUnavailable = false
                if Self.isTerminal(job.lifecycleState) {
                    finishObservation()
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                guard generation == observationGeneration, !Task.isCancelled else { return }
                // Transient transport failure: retain the known job; the loop
                // continues on the next tick.
                progressUnavailable = true
            }
        }
    }

    /// A terminal state ends observation and refreshes what the screen shows.
    private func finishObservation() {
        observationGeneration += 1
        observedJobID = nil
        observationTask = nil
        Task { await load() }
    }

    /// Attach on presentation: if no observation is running, adopt the
    /// current/latest job and resume observing it (#170). Idempotent — a
    /// running loop is left alone.
    func attachObservation() async {
        if let task = observationTask, !task.isCancelled { return }
        observationGeneration += 1
        let generation = observationGeneration
        do {
            let job = try await api.applyStatus(jobID: nil)
            guard generation == observationGeneration else { return }
            guard job.jobId != nil else { return }  // explicit idle read
            if Self.isTerminal(job.lifecycleState) {
                // Show the last outcome (its recovery may need attention)
                // without polling; a fresh apply replaces it.
                applyJob = job
                return
            }
            beginObservation(job)
        } catch is CancellationError {
            return
        } catch {
            // No job known — the screen stays usable; Apply stays enabled.
        }
    }

    /// Detach on dismissal: stop OBSERVING, never the server job (#170).
    func detachObservation() {
        observationTask?.cancel()
        observationTask = nil
        observationGeneration += 1
        observedJobID = nil
    }

    var applyBanner: String? {
        switch applyPhase {
        case .preflight:
            return String(localized: "Checking model and deployment compatibility…")
        case .preparing:
            return String(
                localized:
                    "Preparing — downloading the new model. The current deployment is not replaced yet."
            )
        case .replacing:
            return String(
                localized:
                    "Replacing and validating the new deployment. The advisor may temporarily answer with deterministic fallbacks."
            )
        case .commitPending:
            return String(localized: "Verified — saving the household selection…")
        case .succeeded:
            return String(
                localized: "✅ The requested configuration is active; the selection is saved.")
        case .failed:
            return String(
                localized: "❌ The replacement failed before anything was changed.")
        case .restoring:
            return String(
                localized: "The replacement failed — restoring the previous configuration…")
        case .restored:
            return String(
                localized: "↩️ The previous configuration is verified and running again.")
        case .recovery:
            return String(localized: "⚠️ Recovery needs attention.")
        case nil:
            return nil
        }
    }

    var applyBannerIsFailure: Bool { applyPhase?.isFailure ?? false }

    var statusLine: String {
        guard let status else { return String(localized: "Checking…") }
        if status.ready, let served = status.servedModel {
            return String(localized: "Answering with \(served)")
        }
        if let phase = status.loadingDetail, !phase.isEmpty {
            return String(localized: "Loading — \(phase)")
        }
        return status.detail
    }
}
