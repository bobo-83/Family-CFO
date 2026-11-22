import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockAIRuntimeAPI: AIRuntimeAPI, @unchecked Sendable {
    var statusResult = Components.Schemas.AiRuntimeStatus(
        enabled: true, provider: "vllm", model: "Qwen/Current", ready: true,
        servedModel: "Qwen/Current", detail: "loaded",
        visionReady: true, visionModel: "Qwen/Vision", visionEnabled: true)
    var catalogResult: [Components.Schemas.AiModelInfo] = []
    var hardwareResult = Components.Schemas.AiHardwareProfile(
        gpuMemoryGb: 100, systemMemoryGb: 128, diskFreeGb: 500, source: "test")
    var configResult = Components.Schemas.AiRuntimeConfig(
        provider: .vllm, baseUrl: "http://vllm:8000", model: "Qwen/Current", enabled: true)
    // nil = the server 403s (no AI-runtime-manage right), like production.
    var usageResult: Components.Schemas.AiUsageResponse?
    /// The current/latest job read (`applyStatus(jobID: nil)`); nil = 404 (no job).
    var currentJobResult: Components.Schemas.AiApplyJobStatus?
    /// Jobs returned by `apply`, popped in order; empty = a default accepted job.
    var applyResponses: [Components.Schemas.AiApplyJobStatus] = []
    /// Jobs returned by `retryRecovery`, popped in order.
    var recoveryResponses: [Components.Schemas.AiApplyJobStatus] = []

    private(set) var applyRequests: [Components.Schemas.AiApplyRequest] = []
    private(set) var recoveryCalls: [(jobID: UUID, requestID: UUID)] = []
    private(set) var applyStatusCalls: [UUID?] = []
    private(set) var statusFetches = 0

    // Exact-job polls gate on a continuation so tests drive every tick.
    private var statusWaiters: [CheckedContinuation<Components.Schemas.AiApplyJobStatus, Error>] = []
    private var queuedStatusResults: [Result<Components.Schemas.AiApplyJobStatus, Error>] = []

    func status() async throws -> Components.Schemas.AiRuntimeStatus {
        statusFetches += 1
        return statusResult
    }
    func catalog() async throws -> [Components.Schemas.AiModelInfo] { catalogResult }
    func hardware() async throws -> Components.Schemas.AiHardwareProfile { hardwareResult }
    func runtimeConfig() async throws -> Components.Schemas.AiRuntimeConfig { configResult }
    func updateRuntimeConfig(_ config: Components.Schemas.AiRuntimeConfig) async throws
        -> Components.Schemas.AiRuntimeConfig
    {
        configResult = config
        return configResult
    }
    func search(query: String) async throws -> [Components.Schemas.AiModelInfo] {
        catalogResult.filter { $0.label.contains(query) }
    }
    func detail(id: String) async throws -> Components.Schemas.AiModelDetail {
        guard let info = catalogResult.first(where: { $0.id == id }) else {
            throw APIError.server(404)
        }
        return Components.Schemas.AiModelDetail(info: info, downloads: 1000, likes: 10)
    }
    func apply(request: Components.Schemas.AiApplyRequest) async throws
        -> Components.Schemas.AiApplyJobStatus
    {
        applyRequests.append(request)
        if !applyResponses.isEmpty { return applyResponses.removeFirst() }
        return Components.Schemas.AiApplyJobStatus(
            jobId: "11111111-1111-4111-8111-111111111111", requestId: request.requestId,
            sequence: 1, state: .running, lifecycleState: .accepted,
            mainModel: request.mainModel, visionModel: request.visionModel,
            recoveryAvailable: false)
    }
    func applyStatus(jobID: UUID?) async throws -> Components.Schemas.AiApplyJobStatus {
        applyStatusCalls.append(jobID)
        guard let jobID else {
            // nil = the current/latest read; nil result = no job admitted (404).
            guard let current = currentJobResult else { throw APIError.server(404) }
            return current
        }
        return try await withCheckedThrowingContinuation { continuation in
            if !queuedStatusResults.isEmpty {
                continuation.resume(with: queuedStatusResults.removeFirst())
            } else {
                statusWaiters.append(continuation)
            }
        }
    }
    /// Fulfill the next exact-job poll (or queue it if no poll is waiting yet).
    func fulfillNextStatus(_ result: Result<Components.Schemas.AiApplyJobStatus, Error>) {
        if let waiter = statusWaiters.first {
            statusWaiters.removeFirst()
            waiter.resume(with: result)
        } else {
            queuedStatusResults.append(result)
        }
    }
    func retryRecovery(jobID: UUID, requestID: UUID) async throws
        -> Components.Schemas.AiApplyJobStatus
    {
        recoveryCalls.append((jobID, requestID))
        if !recoveryResponses.isEmpty { return recoveryResponses.removeFirst() }
        return Components.Schemas.AiApplyJobStatus(
            jobId: jobID.uuidString, requestId: requestID.uuidString, sequence: 4,
            state: .running, lifecycleState: .restoring, recoveryAvailable: false)
    }
    func usage() async throws -> Components.Schemas.AiUsageResponse {
        guard let usageResult else { throw APIError.server(403) }
        return usageResult
    }
}

private func model(
    _ id: String, memGb: Double, vision: Bool = false, dual: Bool = false
) -> Components.Schemas.AiModelInfo {
    .init(
        id: id, label: id, role: dual ? .both : (vision ? .vision : .main), parametersB: 35,
        estMemoryGb: memGb, estDiskGb: memGb, toolParser: "hermes",
        supportsVision: vision || dual, gated: false)
}

/// An apply job in a given lifecycle state.
private func job(
    _ id: UUID, sequence: Int,
    _ lifecycle: Components.Schemas.AiApplyJobStatus.LifecycleStatePayload,
    recoveryAvailable: Bool = false, reasonCode: String? = nil, message: String? = nil
) -> Components.Schemas.AiApplyJobStatus {
    let coarse: Components.Schemas.AiApplyJobStatus.StatePayload
    switch lifecycle {
    case .succeeded: coarse = .succeeded
    case .failed, .restored, .recoveryFailed: coarse = .failed
    default: coarse = .running
    }
    return .init(
        jobId: id.uuidString, requestId: "req-\(sequence)", sequence: sequence,
        state: coarse, lifecycleState: lifecycle, mainModel: "Qwen/New",
        reasonCode: reasonCode, message: message, recoveryAvailable: recoveryAvailable)
}

@MainActor
struct AIRuntimeViewModelTests {
    private let jobID = UUID(uuidString: "5f0c3d2e-1a4b-4c8d-9e2f-3a5b6c7d8e9f")!

    /// Let other MainActor tasks (the observation loop, deferred loads) run.
    private func drain(_ times: Int = 10) async {
        for _ in 0..<times { await Task.yield() }
    }
    /// Bounded spin until a condition holds (drains between checks).
    private func waitUntil(_ condition: @MainActor () -> Bool) async {
        for _ in 0..<1_000 {
            if condition() { return }
            await Task.yield()
        }
    }

    // MARK: - Fit verdicts

    @Test func fitVerdictsCompareAgainstTheBoxMemory() async {
        let api = MockAIRuntimeAPI()  // 100 GB GPU budget
        api.catalogResult = [
            model("small", memGb: 40), model("tight", memGb: 90), model("huge", memGb: 200),
        ]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.fit(of: api.catalogResult[0]) == .fits)
        #expect(vm.fit(of: api.catalogResult[1]) == .tight)
        #expect(vm.fit(of: api.catalogResult[2]) == .tooBig)
    }

    @Test func fitIsUnknownWithoutAHardwareProfile() async {
        let api = MockAIRuntimeAPI()
        api.hardwareResult = .init(gpuMemoryGb: nil, systemMemoryGb: nil, diskFreeGb: 1, source: "x")
        api.catalogResult = [model("m", memGb: 40)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.fit(of: api.catalogResult[0]) == .unknown)
    }

    // MARK: - Role intent (#170): one request, explicit photo placement

    @Test func textOnlyMainKeepsTheServedVisionModel() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/New")
        // The killer regression: nil here would silently DISABLE photo scans.
        #expect(request?.visionModel == "Qwen/Vision")
        #expect(request?.photoMode == .separate)
        #expect(request?.topology == .auto)
        #expect(request?.cluster == nil)
    }

    @Test func textOnlyMainWithoutAServedVisionModelSendsPhotosOff() async {
        let api = MockAIRuntimeAPI()
        api.statusResult.visionModel = nil
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/New")
        #expect(request?.visionModel == nil)
        #expect(request?.photoMode == .off)
    }

    @Test func dualCapableMainServesPhotosItself() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/VL-30B", memGb: 33, dual: true)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/VL-30B")
        #expect(request?.visionModel == nil)
        #expect(request?.photoMode == .main)
    }

    @Test func visionOnlyModelPairsWithTheServedMain() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/VL-8B", memGb: 9, vision: true)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/Current")
        #expect(request?.visionModel == "Qwen/VL-8B")
        #expect(request?.photoMode == .separate)
    }

    @Test func visionOnlyModelWithoutACurrentMainIsRefusedLocally() async {
        let api = MockAIRuntimeAPI()
        // Nothing served and an empty configured main = no chat model at all.
        api.statusResult.servedModel = nil
        api.configResult.model = ""
        api.catalogResult = [model("Qwen/VL-8B", memGb: 9, vision: true)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()
        #expect(vm.applyRequest(for: api.catalogResult[0]) == nil)
    }

    @Test func visionOnlyModelFallsBackToTheConfiguredMain() async {
        let api = MockAIRuntimeAPI()
        // Not serving yet — the configured model is still a real chat model.
        api.statusResult.servedModel = nil
        api.catalogResult = [model("Qwen/VL-8B", memGb: 9, vision: true)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/Current")
        #expect(request?.visionModel == "Qwen/VL-8B")
        #expect(request?.photoMode == .separate)
    }

    @Test func sameVisionAndMainIdNormalizesToOneInstance() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/Current", memGb: 33, dual: true)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        let request = vm.applyRequest(for: api.catalogResult[0])
        #expect(request?.mainModel == "Qwen/Current")
        #expect(request?.visionModel == nil)
        // ONE instance, never two copies of the same model.
        #expect(request?.photoMode == .main)
    }

    // MARK: - One apply/observation lane (#170)

    @Test func applyPostsOneRequestAndObservationFollowsToSucceeded() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        let fetchesBeforeApply = api.statusFetches

        await vm.apply(api.catalogResult[0])

        // Exactly one POST, topology auto, a fresh request_id, no deprecated flag.
        #expect(api.applyRequests.count == 1)
        let request = api.applyRequests[0]
        #expect(request.mainModel == "Qwen/New")
        #expect(request.visionModel == "Qwen/Vision")
        #expect(request.photoMode == .separate)
        #expect(request.topology == .auto)
        #expect(request.requestId != nil)
        #expect(vm.applyPhase == .preparing)
        #expect(vm.replacementInFlight)

        // Poll 1: verifying.
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .verifying)))
        await drain()
        #expect(vm.applyPhase == .replacing)
        #expect(vm.replacementInFlight)

        // Poll 2: succeeded — terminal, observation stops, screen refreshes.
        api.fulfillNextStatus(.success(job(jobID, sequence: 3, .succeeded)))
        await waitUntil { vm.applyPhase == .succeeded }
        #expect(vm.applyPhase == .succeeded)
        #expect(!vm.replacementInFlight)
        await waitUntil { api.statusFetches > fetchesBeforeApply }  // refresh after terminal

        // Observation ended: a late poll result never moves the state back.
        let polls = api.applyStatusCalls.count
        api.fulfillNextStatus(.success(job(jobID, sequence: 4, .verifying)))
        await drain()
        #expect(vm.applyPhase == .succeeded)
        #expect(api.applyStatusCalls.count == polls)
    }

    @Test func tooBigEstimateDoesNotBlockApply() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/Big-72B", memGb: 200)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        #expect(vm.fit(of: api.catalogResult[0]) == .tooBig)

        // The fit badge is an ESTIMATE; the server validates before switching.
        await vm.apply(api.catalogResult[0])
        #expect(api.applyRequests.count == 1)
    }

    @Test func applyIsRefusedWhileAReplacementIsUnfinishedAndReenabledAfterTerminal() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("A", memGb: 40), model("B", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()

        await vm.apply(api.catalogResult[0])
        #expect(api.applyRequests.count == 1)

        // Unfinished replacement: the second apply is refused locally.
        await vm.apply(api.catalogResult[1])
        #expect(api.applyRequests.count == 1)

        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .succeeded)))
        await waitUntil { vm.applyPhase == .succeeded }
        await waitUntil { !vm.replacementInFlight }

        await vm.apply(api.catalogResult[1])
        #expect(api.applyRequests.count == 2)
    }

    // MARK: - Observation guards: job id, sequence, transport failures

    @Test func outOfOrderAndForeignJobPollsAreIgnored() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        await vm.apply(api.catalogResult[0])
        await drain()
        #expect(vm.applyJob?.sequence == 1)

        // A different job's poll result never touches this observation...
        let otherID = UUID()
        api.fulfillNextStatus(.success(job(otherID, sequence: 9, .succeeded)))
        await drain()
        #expect(vm.applyJob?.sequence == 1)
        #expect(vm.applyPhase == .preparing)

        // ...nor do duplicate or decreasing sequence numbers of THIS job.
        api.fulfillNextStatus(.success(job(jobID, sequence: 1, .accepted)))
        await drain()
        #expect(vm.applyJob?.sequence == 1)
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .verifying)))
        await drain()
        #expect(vm.applyJob?.sequence == 2)
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .verifying)))
        await drain()
        #expect(vm.applyJob?.sequence == 2)

        api.fulfillNextStatus(.success(job(jobID, sequence: 3, .succeeded)))
        await waitUntil { vm.applyPhase == .succeeded }
    }

    @Test func transientPollFailureRetainsTheJobAndShowsProgressUnavailable() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        await vm.apply(api.catalogResult[0])
        await drain()

        // The network drops mid-observation: no failure, no lost job.
        api.fulfillNextStatus(.failure(APIError.server(503)))
        await drain()
        #expect(vm.progressUnavailable)
        #expect(vm.applyPhase == .preparing)
        #expect(vm.applyBannerIsFailure == false)
        #expect(vm.errorMessage == nil)

        // The next successful tick clears it and the job continues.
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .verifying)))
        await drain()
        #expect(vm.progressUnavailable == false)
        #expect(vm.applyPhase == .replacing)
    }

    @Test func detachStopsObservingAndIgnoresStaleFulfillment() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        await vm.apply(api.catalogResult[0])
        await drain()

        // Dismissal stops OBSERVING, never the server job.
        vm.detachObservation()
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .verifying)))
        await drain()
        #expect(vm.applyJob?.sequence == 1)
        #expect(vm.applyPhase == .preparing)
        // Teardown is not a failure: no poll error, no alert.
        #expect(vm.progressUnavailable == false)
        #expect(vm.errorMessage == nil)
    }

    @Test func cancellationDuringPollIsNotAFailure() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 1, .accepted)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .seconds(60)  // the sleep is what gets cancelled
        await vm.load()
        await vm.apply(api.catalogResult[0])
        await drain()

        vm.detachObservation()
        await drain(20)
        // CancellationError is observation teardown — never a replacement failure.
        #expect(vm.applyPhase == .preparing)
        #expect(vm.progressUnavailable == false)
        #expect(vm.errorMessage == nil)
    }

    // MARK: - Screen entry: adopt the current/latest job

    @Test func attachResumesObservingTheCurrentJob() async {
        let api = MockAIRuntimeAPI()
        api.currentJobResult = job(jobID, sequence: 2, .verifying)
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()

        await vm.attachObservation()
        #expect(vm.applyPhase == .replacing)
        #expect(vm.replacementInFlight)

        api.fulfillNextStatus(.success(job(jobID, sequence: 3, .succeeded)))
        await waitUntil { vm.applyPhase == .succeeded }
    }

    @Test func attachShowsTheTerminalOutcomeWithoutPolling() async {
        let api = MockAIRuntimeAPI()
        api.currentJobResult = job(
            jobID, sequence: 3, .failed, reasonCode: "preflight_memory",
            message: "The model does not fit the available memory.")
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()

        await vm.attachObservation()
        #expect(vm.applyPhase == .failed)
        #expect(vm.applyBannerIsFailure)
        #expect(vm.applyFailureDetail == "The model does not fit the available memory.")
        await drain()
        // Terminal: shown without polling — a fresh apply replaces it.
        #expect(api.applyStatusCalls == [nil])
    }

    @Test func attachWithNoJobStaysIdle() async {
        let api = MockAIRuntimeAPI()
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()

        await vm.attachObservation()
        await drain()
        #expect(vm.applyPhase == nil)
        #expect(vm.applyJob == nil)
        #expect(vm.replacementInFlight == false)
        #expect(vm.errorMessage == nil)
    }

    // MARK: - Recovery (#170): retry the frozen direction, never a new model

    @Test func recoveryFailedOffersRetryWithANewRequestIdAndReobserves() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        api.applyResponses = [job(jobID, sequence: 3, .recoveryFailed, recoveryAvailable: true)]
        api.recoveryResponses = [job(jobID, sequence: 4, .restoring)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()
        await vm.apply(api.catalogResult[0])
        await drain()

        #expect(vm.applyPhase == .recovery)
        #expect(vm.applyBannerIsFailure)
        #expect(vm.recoveryAvailable)

        let applyRequestID = api.applyRequests[0].requestId
        await vm.retryRecovery()
        #expect(api.recoveryCalls.count == 1)
        #expect(api.recoveryCalls[0].jobID == jobID)
        // A NEW request_id identifies the attempt — never the job's original one.
        #expect(api.recoveryCalls[0].requestID.uuidString != applyRequestID)
        #expect(vm.applyPhase == .restoring)
        #expect(vm.applyJob?.sequence == 4)

        // The returned job is re-observed to its own terminal state.
        // (Fulfill a stale tick first: it lands on whichever poller is waiting
        // first — a superseded loop ignores it, the fresh one sequence-guards.)
        api.fulfillNextStatus(.success(job(jobID, sequence: 2, .accepted)))
        await drain()
        api.fulfillNextStatus(.success(job(jobID, sequence: 5, .restored)))
        await waitUntil { vm.applyPhase == .restored }
        #expect(vm.applyJob?.sequence == 5)
        #expect(!vm.replacementInFlight)
    }

    // MARK: - Status line: what is ANSWERING, not what is configured

    @Test func statusLineReportsTheServedModelNotTheConfiguredOne() async {
        let api = MockAIRuntimeAPI()
        api.statusResult.model = "Qwen/Configured"
        api.statusResult.servedModel = "Qwen/Served"
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.statusLine.contains("Qwen/Served"))
        #expect(!vm.statusLine.contains("Qwen/Configured"))
    }

    // MARK: - Two-box cluster (ADR 0071): placement stays advisory

    @Test func clusterActiveMovesTooBigModelsToTheClusterList() async {
        let api = MockAIRuntimeAPI()  // 100 GB single-box budget
        api.hardwareResult = .init(
            gpuMemoryGb: 100, systemMemoryGb: 128, diskFreeGb: 500, source: "test",
            clusterPeerHost: "spark-2.local", clusterPeerReachable: true, clusterMemoryGb: 243)
        api.configResult.clusterEnabled = true
        api.catalogResult = [model("small", memGb: 40), model("Qwen/Big-72B", memGb: 145)]
        let vm = AIRuntimeViewModel(api: api)
        vm.pollInterval = .zero
        await vm.load()

        // A min_nodes==1 model that is too big for one box is OFFERED as a
        // cluster deployment: cluster list, combined-budget verdict.
        #expect(vm.clusterModels.map(\.id) == ["Qwen/Big-72B"])
        #expect(vm.singleNodeModels.map(\.id) == ["small"])
        #expect(vm.fit(of: api.catalogResult[1]) == .fits)  // 145 vs 243 combined

        // Placement is the server's call: the client always sends topology
        // auto and never the deprecated cluster flag.
        await vm.apply(api.catalogResult[1])
        #expect(api.applyRequests.count == 1)
        #expect(api.applyRequests[0].topology == .auto)
        #expect(api.applyRequests[0].cluster == nil)
    }

    @Test func clusterInactiveKeepsTodaysSingleBoxBehavior() async {
        let api = MockAIRuntimeAPI()  // 100 GB budget, no cluster fields
        let big = model("Qwen/Big-72B", memGb: 145)
        api.catalogResult = [big]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.clusterModels.isEmpty)
        #expect(vm.singleNodeModels.map(\.id) == ["Qwen/Big-72B"])
        #expect(vm.fit(of: big) == .tooBig)  // honest single-box verdict

        // min_nodes >= 2 entries stay hidden entirely while clustering is off.
        var hidden = big
        hidden.minNodes = 2
        api.catalogResult = [hidden]
        await vm.load()
        #expect(vm.singleNodeModels.isEmpty)
        #expect(vm.clusterModels.isEmpty)
    }

    // MARK: - Household usage (#181)

    @Test func householdUsageShowsWithTwoHouseholdsInServerOrder() async {
        let api = MockAIRuntimeAPI()
        api.usageResult = .init(
            households: [
                .init(
                    householdId: "hh-cedar", name: "Cedar family", chats24h: 5, chats7d: 12,
                    medianAnswerMs: 8300, storageBytes: 1_200_000_000),
                .init(
                    householdId: "hh-birch", name: "Birch family", chats24h: 0, chats7d: 1,
                    medianAnswerMs: nil, storageBytes: 340_000_000),
            ],
            chatHourlyLimit: 30)
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.showsHouseholdUsage)
        // Server pre-sorts heaviest first; the client must not reorder.
        #expect(vm.usage?.households.map(\.name) == ["Cedar family", "Birch family"])
        #expect(vm.fairUseCapLabel == "Fair-use cap: 30 chats/hour per household")
    }

    @Test func householdUsageHiddenForASingleFamilyWithTheCapOff() async {
        let api = MockAIRuntimeAPI()
        api.usageResult = .init(
            households: [
                .init(
                    householdId: "hh-cedar", name: "Cedar family", chats24h: 2, chats7d: 9,
                    medianAnswerMs: 4200, storageBytes: 90_000_000)
            ],
            chatHourlyLimit: 0)
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(!vm.showsHouseholdUsage)

        // Arming the cap makes the single-household view meaningful again.
        api.usageResult?.chatHourlyLimit = 12
        await vm.load()
        #expect(vm.showsHouseholdUsage)
        #expect(vm.fairUseCapLabel == "Fair-use cap: 12 chats/hour per household")
    }

    @Test func usage403FailsQuietAndLeavesTheLoadHealthy() async {
        let api = MockAIRuntimeAPI()  // usageResult nil -> the endpoint 403s
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.usage == nil)
        #expect(!vm.showsHouseholdUsage)
        #expect(vm.errorMessage == nil)  // telemetry must never break the page
    }

    @Test func memberKeyMissingFlagsTheFootnoteOnlyWhenAHouseholdNeedsSignIn() async {
        let api = MockAIRuntimeAPI()
        api.usageResult = .init(
            households: [
                .init(
                    householdId: "hh-cedar", name: "Cedar family", chats24h: 5, chats7d: 12,
                    medianAnswerMs: 8300, storageBytes: 1_200_000_000, memberKeyOk: true),
                .init(
                    householdId: "hh-birch", name: "Birch family", chats24h: 0, chats7d: 1,
                    medianAnswerMs: nil, storageBytes: 340_000_000, memberKeyOk: false),
            ],
            chatHourlyLimit: 30)
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()
        #expect(vm.anyHouseholdNeedsMemberSignIn)

        // All members signed in -> no footnote.
        api.usageResult?.households[1].memberKeyOk = true
        await vm.load()
        #expect(!vm.anyHouseholdNeedsMemberSignIn)
    }

    @Test func storageLabelUsesHumanMbAndGb() {
        #expect(AIRuntimeViewModel.storageLabel(bytes: 1_200_000_000) == "1.2 GB")
        #expect(AIRuntimeViewModel.storageLabel(bytes: 340_000_000) == "340 MB")
        #expect(AIRuntimeViewModel.storageLabel(bytes: 0) == "0 MB")
    }
}
