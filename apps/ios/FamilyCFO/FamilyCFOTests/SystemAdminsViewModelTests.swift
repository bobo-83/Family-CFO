import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockSystemAdminsAPI: SystemAdminsAPI, @unchecked Sendable {
    var admins: [Components.Schemas.SystemAdmin] = []
    var grantError: Error?
    private(set) var granted: [String] = []
    private(set) var revoked: [String] = []

    nonisolated func list() async throws -> [Components.Schemas.SystemAdmin] {
        await MainActor.run { admins }
    }

    nonisolated func grant(email: String) async throws -> Components.Schemas.SystemAdmin {
        try await MainActor.run {
            if let grantError { throw grantError }
            granted.append(email)
            let admin = Components.Schemas.SystemAdmin(
                userId: "u-\(granted.count)", email: email, displayName: email,
                grantedAt: Date())
            admins.append(admin)
            return admin
        }
    }

    nonisolated func revoke(userID: String) async throws {
        await MainActor.run {
            revoked.append(userID)
            admins.removeAll { $0.userId == userID }
        }
    }
}

@MainActor
struct SystemAdminsViewModelTests {
    private func admin(_ id: String, email: String) -> Components.Schemas.SystemAdmin {
        .init(userId: id, email: email, displayName: email, grantedAt: Date())
    }

    @Test func loadsTheRoster() async {
        let api = MockSystemAdminsAPI()
        api.admins = [admin("u1", email: "owner@x.com")]
        let viewModel = SystemAdminsViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.admins.count == 1)
        #expect(viewModel.canRevoke == false)  // never offer to empty the roster
    }

    @Test func grantNormalizesTheEmailAndReloads() async {
        let api = MockSystemAdminsAPI()
        api.admins = [admin("u1", email: "owner@x.com")]
        let viewModel = SystemAdminsViewModel(api: api)
        await viewModel.load()

        await viewModel.grant(email: "  Gerda@Yahoo.com ")

        #expect(api.granted == ["gerda@yahoo.com"])
        #expect(viewModel.admins.count == 2)
        #expect(viewModel.canRevoke)
    }

    @Test func revokeIsRefusedClientSideForTheLastAdmin() async {
        let api = MockSystemAdminsAPI()
        api.admins = [admin("u1", email: "owner@x.com")]
        let viewModel = SystemAdminsViewModel(api: api)
        await viewModel.load()

        await viewModel.revoke(userID: "u1")

        #expect(api.revoked.isEmpty)
        #expect(viewModel.admins.count == 1)
    }

    @Test func aGrantFailureSurfacesTheServersOwnMessage() async {
        let api = MockSystemAdminsAPI()
        api.admins = [admin("u1", email: "owner@x.com")]
        api.grantError = APIError.advisor("No user with that email — invite them to the household first.")
        let viewModel = SystemAdminsViewModel(api: api)
        await viewModel.load()

        await viewModel.grant(email: "nobody@x.com")

        #expect(viewModel.errorMessage?.contains("No user with that email") == true)
    }
}
