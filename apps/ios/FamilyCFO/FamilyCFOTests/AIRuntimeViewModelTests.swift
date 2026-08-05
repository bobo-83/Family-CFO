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
    var applyStates: [Components.Schemas.AiSwapStatus] = []
    var configResult = Components.Schemas.AiRuntimeConfig(
        provider: .vllm, baseUrl: "http://vllm:8000", model: "Qwen/Current", enabled: true)
    // nil = the server 403s (no AI-runtime-manage right), like production.
    var usageResult: Components.Schemas.AiUsageResponse?
    private(set) var appliedMain: String?
    private(set) var appliedVision: String?
    private(set) var appliedCluster: Bool?

    nonisolated func status() async throws -> Components.Schemas.AiRuntimeStatus {
        await MainActor.run { statusResult }
    }
    nonisolated func catalog() async throws -> [Components.Schemas.AiModelInfo] {
        await MainActor.run { catalogResult }
    }
    nonisolated func hardware() async throws -> Components.Schemas.AiHardwareProfile {
        await MainActor.run { hardwareResult }
    }
    nonisolated func runtimeConfig() async throws -> Components.Schemas.AiRuntimeConfig {
        await MainActor.run { configResult }
    }
    nonisolated func updateRuntimeConfig(_ config: Components.Schemas.AiRuntimeConfig) async throws
        -> Components.Schemas.AiRuntimeConfig
    {
        await MainActor.run {
            configResult = config
            return configResult
        }
    }
    nonisolated func search(query: String) async throws -> [Components.Schemas.AiModelInfo] {
        await MainActor.run { catalogResult.filter { $0.label.contains(query) } }
    }
    nonisolated func detail(id: String) async throws -> Components.Schemas.AiModelDetail {
        let info = await MainActor.run { catalogResult.first { $0.id == id } }
        guard let info else { throw APIError.server(404) }
        return Components.Schemas.AiModelDetail(info: info, downloads: 1000, likes: 10)
    }
    nonisolated func apply(mainModel: String, visionModel: String?, cluster: Bool) async throws
        -> Components.Schemas.AiSwapStatus
    {
        await MainActor.run {
            appliedMain = mainModel
            appliedVision = visionModel
            appliedCluster = cluster
            return applyStates.first
                ?? Components.Schemas.AiSwapStatus(state: .succeeded, mainModel: mainModel)
        }
    }
    nonisolated func applyStatus() async throws -> Components.Schemas.AiSwapStatus {
        await MainActor.run {
            if applyStates.count > 1 { applyStates.removeFirst() }
            return applyStates.first
                ?? Components.Schemas.AiSwapStatus(state: .succeeded)
        }
    }
    nonisolated func usage() async throws -> Components.Schemas.AiUsageResponse {
        guard let result = await MainActor.run(body: { usageResult }) else {
            throw APIError.server(403)
        }
        return result
    }
}

private func model(
    _ id: String, memGb: Double, vision: Bool = false
) -> Components.Schemas.AiModelInfo {
    .init(
        id: id, label: id, role: vision ? .vision : .main, parametersB: 35,
        estMemoryGb: memGb, estDiskGb: memGb, toolParser: "hermes",
        supportsVision: vision, gated: false)
}

@MainActor
struct AIRuntimeViewModelTests {
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

    @Test func applyKeepsTheCurrentVisionModel() async {
        let api = MockAIRuntimeAPI()
        api.catalogResult = [model("Qwen/New", memGb: 40)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        await vm.apply(api.catalogResult[0])

        #expect(api.appliedMain == "Qwen/New")
        // The killer regression: passing nil would silently DISABLE photo scans.
        #expect(api.appliedVision == "Qwen/Vision")
        #expect(vm.applyState?.state == .succeeded)
    }

    @Test func clusterActiveMovesTooBigModelsToTheClusterListAndAppliesWithCluster() async {
        let api = MockAIRuntimeAPI()  // 100 GB single-box budget
        api.hardwareResult = .init(
            gpuMemoryGb: 100, systemMemoryGb: 128, diskFreeGb: 500, source: "test",
            clusterPeerHost: "spark-2.local", clusterPeerReachable: true, clusterMemoryGb: 243)
        api.configResult.clusterEnabled = true
        api.catalogResult = [model("small", memGb: 40), model("Qwen/Big-72B", memGb: 145)]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        // A min_nodes==1 model that is too big for one box is OFFERED as a
        // cluster deployment: cluster list, combined-budget verdict, and the
        // apply carries cluster: true.
        #expect(vm.clusterModels.map(\.id) == ["Qwen/Big-72B"])
        #expect(vm.singleNodeModels.map(\.id) == ["small"])
        #expect(vm.fit(of: api.catalogResult[1]) == .fits)  // 145 vs 243 combined

        await vm.apply(api.catalogResult[1])
        #expect(api.appliedMain == "Qwen/Big-72B")
        #expect(api.appliedCluster == true)

        await vm.apply(api.catalogResult[0])
        #expect(api.appliedCluster == false)  // single-box applies never cluster
    }

    @Test func clusterInactiveKeepsTodaysSingleBoxBehavior() async {
        let api = MockAIRuntimeAPI()  // 100 GB budget, no cluster fields
        var big = model("Qwen/Big-72B", memGb: 145)
        api.catalogResult = [big]
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()

        #expect(vm.clusterModels.isEmpty)
        #expect(vm.singleNodeModels.map(\.id) == ["Qwen/Big-72B"])
        #expect(vm.fit(of: big) == .tooBig)  // honest single-box verdict

        // min_nodes >= 2 entries stay hidden entirely while clustering is off.
        big.minNodes = 2
        api.catalogResult = [big]
        await vm.load()
        #expect(vm.singleNodeModels.isEmpty)
        #expect(vm.clusterModels.isEmpty)
    }

    @Test func statusLineReportsServedModel() async {
        let api = MockAIRuntimeAPI()
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()
        #expect(vm.statusLine.contains("Qwen/Current"))
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
