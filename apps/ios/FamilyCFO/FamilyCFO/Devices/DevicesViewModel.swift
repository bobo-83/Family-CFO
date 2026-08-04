import Foundation
import Observation

/// Drives the Devices screen (ADR 0025 parity with the dashboard's Devices
/// page): every phone paired to the household, with revoke for holders of
/// `devices.manage`.
@MainActor
@Observable
final class DevicesViewModel {
    private let api: DevicesAPI
    /// ADR 0034: `devices.manage` — without it the revoke affordance is absent.
    let canRevoke: Bool
    /// The paired device backing this session (SessionInfo.device_id, cached
    /// in the stored credential since pairing). Pinned first, badged, and
    /// never offered revoke — revoking it would end this very session, and
    /// the server 409s the attempt anyway.
    private let currentDeviceID: String?

    /// Active devices newest-first, then revoked ones (dimmed at the bottom).
    private(set) var devices: [Components.Schemas.PairedDevice] = []
    private(set) var isLoading = false
    private(set) var isRevoking = false
    var errorMessage: String?

    init(api: DevicesAPI, canRevoke: Bool, currentDeviceID: String?) {
        self.api = api
        self.canRevoke = canRevoke
        self.currentDeviceID = currentDeviceID
    }

    func isCurrentDevice(_ device: Components.Schemas.PairedDevice) -> Bool {
        device.id == currentDeviceID
    }

    func canRevoke(_ device: Components.Schemas.PairedDevice) -> Bool {
        canRevoke && device.revokedAt == nil && !isCurrentDevice(device)
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            devices = Self.sorted(try await api.list(), currentDeviceID: currentDeviceID)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func revoke(_ device: Components.Schemas.PairedDevice) async {
        guard canRevoke(device), !isRevoking else { return }
        isRevoking = true
        defer { isRevoking = false }
        do {
            try await api.revoke(deviceID: device.id)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Active before revoked; this session's device first among the active,
    /// then newest pairing first within each group.
    static func sorted(
        _ devices: [Components.Schemas.PairedDevice], currentDeviceID: String?
    ) -> [Components.Schemas.PairedDevice] {
        devices.sorted { a, b in
            if (a.revokedAt == nil) != (b.revokedAt == nil) { return a.revokedAt == nil }
            if (a.id == currentDeviceID) != (b.id == currentDeviceID) {
                return a.id == currentDeviceID
            }
            return a.createdAt > b.createdAt
        }
    }
}
