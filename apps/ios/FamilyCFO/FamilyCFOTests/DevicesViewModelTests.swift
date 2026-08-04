import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockDevicesAPI: DevicesAPI, @unchecked Sendable {
    var devices: [Components.Schemas.PairedDevice] = []
    var actionError: Error?
    private(set) var revoked: [String] = []
    private(set) var listCalls = 0

    nonisolated func list() async throws -> [Components.Schemas.PairedDevice] {
        try await MainActor.run {
            listCalls += 1
            return devices
        }
    }

    nonisolated func revoke(deviceID: String) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            revoked.append(deviceID)
            if let i = devices.firstIndex(where: { $0.id == deviceID }) {
                devices[i].revokedAt = Date()
            }
        }
    }
}

@MainActor
struct DevicesViewModelTests {
    private static func device(
        _ id: String, _ name: String, pairedDaysAgo: Double, revoked: Bool = false
    ) -> Components.Schemas.PairedDevice {
        .init(
            id: id,
            userId: "u1",
            name: name,
            createdAt: Date(timeIntervalSinceNow: -pairedDaysAgo * 86_400),
            lastSeenAt: nil,
            revokedAt: revoked ? Date(timeIntervalSinceNow: -3_600) : nil)
    }

    private func loaded(
        canRevoke: Bool = true, currentDeviceID: String? = "d-this"
    ) async -> (DevicesViewModel, MockDevicesAPI) {
        let api = MockDevicesAPI()
        api.devices = [
            Self.device("d-ipad", "Old kitchen iPad", pairedDaysAgo: 400, revoked: true),
            Self.device("d-spare", "Spare phone", pairedDaysAgo: 90),
            Self.device("d-this", "This phone", pairedDaysAgo: 30),
            Self.device("d-new", "Newest phone", pairedDaysAgo: 1),
        ]
        let vm = DevicesViewModel(
            api: api, canRevoke: canRevoke, currentDeviceID: currentDeviceID)
        await vm.load()
        return (vm, api)
    }

    @Test func loadPinsTheCurrentDeviceThenActiveNewestFirstThenRevoked() async {
        let (vm, _) = await loaded()

        #expect(vm.devices.map(\.id) == ["d-this", "d-new", "d-spare", "d-ipad"])
        #expect(vm.devices.last?.revokedAt != nil)
    }

    @Test func theCurrentDeviceIsMarkedAndOffersNoRevoke() async {
        let (vm, api) = await loaded()

        let current = vm.devices[0]
        #expect(vm.isCurrentDevice(current))
        #expect(!vm.canRevoke(current))
        #expect(vm.devices.dropFirst().allSatisfy { !vm.isCurrentDevice($0) })
        // Even a programmatic revoke of the current device never reaches the API.
        await vm.revoke(current)
        #expect(api.revoked.isEmpty)
        #expect(vm.errorMessage == nil)
    }

    @Test func revokeCallsTheAPIAndReloads() async {
        let (vm, api) = await loaded()
        let spare = vm.devices.first { $0.id == "d-spare" }!

        await vm.revoke(spare)

        #expect(api.revoked == ["d-spare"])
        #expect(api.listCalls == 2)  // initial load + reload after revoking
        // Reloaded: the freshly revoked device now sits with the revoked group.
        #expect(vm.devices.filter { $0.revokedAt == nil }.map(\.id) == ["d-this", "d-new"])
        #expect(vm.errorMessage == nil)
    }

    @Test func withoutTheRightRevokeNeverCallsTheAPI() async {
        let (vm, api) = await loaded(canRevoke: false)

        await vm.revoke(vm.devices.first { $0.id == "d-new" }!)

        #expect(api.revoked.isEmpty)
        #expect(api.listCalls == 1)
        #expect(vm.errorMessage == nil)
    }

    @Test func aFailedRevokeSurfacesAnErrorAndKeepsTheList() async {
        let (vm, api) = await loaded()
        api.actionError = APIError.server(500)

        await vm.revoke(vm.devices.first { $0.id == "d-new" }!)

        #expect(vm.errorMessage != nil)
        #expect(vm.devices.map(\.id) == ["d-this", "d-new", "d-spare", "d-ipad"])
    }

    // A stale list can still offer a device the server knows is current —
    // the 409's own message must reach the alert verbatim.
    @Test func aConflictSurfacesTheServersMessageVerbatim() async {
        let (vm, api) = await loaded()
        let detail =
            "You can't revoke the device you're using. Revoke it from another device or the web dashboard."
        api.actionError = APIError.conflict(detail)

        await vm.revoke(vm.devices.first { $0.id == "d-new" }!)

        #expect(vm.errorMessage == detail)
        #expect(vm.devices.map(\.id) == ["d-this", "d-new", "d-spare", "d-ipad"])
    }

    @Test func aFailedLoadSurfacesAnError() async {
        struct FailingAPI: DevicesAPI {
            func list() async throws -> [Components.Schemas.PairedDevice] {
                throw APIError.server(500)
            }
            func revoke(deviceID: String) async throws {}
        }
        let vm = DevicesViewModel(api: FailingAPI(), canRevoke: true, currentDeviceID: "d-this")

        await vm.load()

        #expect(vm.errorMessage != nil)
        #expect(vm.devices.isEmpty)
    }

    @Test func neverUsedAndLastSeenCaptions() {
        var device = Self.device("d1", "Old kitchen iPad", pairedDaysAgo: 10)
        #expect(DevicesView.caption(for: device).hasSuffix("never used"))

        device.lastSeenAt = Date(timeIntervalSinceNow: -2 * 86_400)
        let caption = DevicesView.caption(for: device)
        #expect(caption.contains("last seen"))
        #expect(!caption.contains("never used"))
    }
}
