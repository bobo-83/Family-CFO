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
    private(set) var appliedMain: String?
    private(set) var appliedVision: String?

    nonisolated func status() async throws -> Components.Schemas.AiRuntimeStatus {
        await MainActor.run { statusResult }
    }
    nonisolated func catalog() async throws -> [Components.Schemas.AiModelInfo] {
        await MainActor.run { catalogResult }
    }
    nonisolated func hardware() async throws -> Components.Schemas.AiHardwareProfile {
        await MainActor.run { hardwareResult }
    }
    nonisolated func search(query: String) async throws -> [Components.Schemas.AiModelInfo] {
        await MainActor.run { catalogResult.filter { $0.label.contains(query) } }
    }
    nonisolated func apply(mainModel: String, visionModel: String?) async throws
        -> Components.Schemas.AiSwapStatus
    {
        await MainActor.run {
            appliedMain = mainModel
            appliedVision = visionModel
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

    @Test func statusLineReportsServedModel() async {
        let api = MockAIRuntimeAPI()
        let vm = AIRuntimeViewModel(api: api)
        await vm.load()
        #expect(vm.statusLine.contains("Qwen/Current"))
    }
}
