import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockHouseholdsAPI: HouseholdsAPI, @unchecked Sendable {
    var households: [Components.Schemas.HostedHousehold] = []
    var offboxRetentionDays = 0
    var createError: Error?
    var createResponse: Components.Schemas.HostedHouseholdCreateResponse?
    var deleteError: Error?
    private(set) var created: [(displayName: String, baseCurrency: String, ownerEmail: String)] = []
    private(set) var deleted: [String] = []
    private(set) var listCalls = 0

    nonisolated func list() async throws -> Components.Schemas.HostedHouseholdList {
        try await MainActor.run {
            listCalls += 1
            return .init(
                households: households,
                offboxBackupRetentionDays: offboxRetentionDays
            )
        }
    }

    nonisolated func create(
        displayName: String, baseCurrency: String, ownerEmail: String
    ) async throws -> Components.Schemas.HostedHouseholdCreateResponse {
        try await MainActor.run {
            if let createError { throw createError }
            created.append((displayName, baseCurrency, ownerEmail))
            guard let createResponse else { throw APIError.server(500) }
            return createResponse
        }
    }

    nonisolated func delete(householdID: String) async throws {
        try await MainActor.run {
            if let deleteError { throw deleteError }
            deleted.append(householdID)
            households.removeAll { $0.id == householdID }
        }
    }
}

@MainActor
struct HouseholdsViewModelTests {
    private static let serverBaseURL = URL(string: "https://box.example.test:8443/api/v1")!

    private static func household(
        _ id: String, _ name: String, pendingInvite: Bool = false, sealed: Bool = false,
        members: Int = 2
    ) -> Components.Schemas.HostedHousehold {
        .init(
            id: id,
            name: name,
            baseCurrency: "USD",
            createdAt: Date(timeIntervalSinceNow: -30 * 86_400),
            memberCount: members,
            pendingOwnerInvite: pendingInvite,
            sealed: sealed)
    }

    private func makeViewModel(
        _ api: MockHouseholdsAPI
    ) -> HouseholdsViewModel {
        HouseholdsViewModel(api: api, serverBaseURL: Self.serverBaseURL)
    }

    @Test func loadListsEveryHostedHousehold() async {
        let api = MockHouseholdsAPI()
        api.households = [
            Self.household("h-cedar", "Cedar family", pendingInvite: true, members: 0),
            Self.household("h-birch", "Birch family", sealed: true, members: 3),
        ]
        let vm = makeViewModel(api)

        await vm.load()

        #expect(vm.households.map(\.id) == ["h-cedar", "h-birch"])
        #expect(vm.households[0].pendingOwnerInvite)
        #expect(vm.households[1].sealed)
        #expect(vm.errorMessage == nil)
    }

    @Test func createReturnsTheOneTimeJoinLinkAndReloads() async {
        let api = MockHouseholdsAPI()
        let expiry = Date(timeIntervalSinceNow: 7 * 86_400)
        api.createResponse = .init(
            household: Self.household("h-cedar", "Cedar family", pendingInvite: true, members: 0),
            inviteToken: "one-time-secret",
            inviteExpiresAt: expiry)
        api.households = [Self.household("h-cedar", "Cedar family", pendingInvite: true, members: 0)]
        let vm = makeViewModel(api)

        let ok = await vm.create(
            displayName: " Cedar family ", baseCurrency: "usd", ownerEmail: " cedar@example.test ")

        #expect(ok)
        // Inputs are trimmed and the currency normalized before the API call.
        #expect(api.created.count == 1)
        #expect(api.created[0].displayName == "Cedar family")
        #expect(api.created[0].baseCurrency == "USD")
        #expect(api.created[0].ownerEmail == "cedar@example.test")
        // The join link mirrors the dashboard's: origin + /join with the token
        // in the fragment, never a query param.
        #expect(
            vm.invite?.joinURL.absoluteString
                == "https://box.example.test:8443/join#token=one-time-secret")
        #expect(vm.invite?.ownerEmail == "cedar@example.test")
        #expect(vm.invite?.expiresAt == expiry)
        #expect(api.listCalls == 1)  // the roster reloads after a create
        #expect(vm.households.map(\.id) == ["h-cedar"])
        #expect(vm.errorMessage == nil)
    }

    @Test func aConflictSurfacesTheServersMessageVerbatim() async {
        let api = MockHouseholdsAPI()
        let detail =
            "That email already has an account on this box — invite them from their existing household instead."
        api.createError = APIError.conflict(detail)
        let vm = makeViewModel(api)

        let ok = await vm.create(
            displayName: "Cedar family", baseCurrency: "USD", ownerEmail: "cedar@example.test")

        #expect(!ok)
        #expect(vm.errorMessage == detail)
        #expect(vm.invite == nil)
    }

    @Test func aFailedLoadSurfacesAnError() async {
        struct FailingAPI: HouseholdsAPI {
            func list() async throws -> Components.Schemas.HostedHouseholdList {
                throw APIError.server(403)
            }
            func create(
                displayName: String, baseCurrency: String, ownerEmail: String
            ) async throws -> Components.Schemas.HostedHouseholdCreateResponse {
                throw APIError.server(403)
            }
            func delete(householdID: String) async throws {
                throw APIError.server(403)
            }
        }
        let vm = HouseholdsViewModel(api: FailingAPI(), serverBaseURL: Self.serverBaseURL)

        await vm.load()

        #expect(vm.errorMessage != nil)
        #expect(vm.households.isEmpty)
    }

    // --- Delete household (#189) ---

    @Test func deleteCallsTheAPIAndReloadsTheRoster() async {
        let api = MockHouseholdsAPI()
        api.households = [
            Self.household("h-cedar", "Cedar family"),
            Self.household("h-birch", "Birch family"),
        ]
        let vm = HouseholdsViewModel(
            api: api, serverBaseURL: Self.serverBaseURL, currentHouseholdID: "h-birch")
        await vm.load()

        await vm.delete(Self.household("h-cedar", "Cedar family"))

        #expect(api.deleted == ["h-cedar"])
        #expect(api.listCalls == 2)  // initial load + the reload after the delete
        #expect(vm.households.map(\.id) == ["h-birch"])
        #expect(vm.errorMessage == nil)
    }

    @Test func theCurrentHouseholdNeverOffersDelete() async {
        let api = MockHouseholdsAPI()
        let vm = HouseholdsViewModel(
            api: api, serverBaseURL: Self.serverBaseURL, currentHouseholdID: "h-birch")

        #expect(!vm.canDelete(Self.household("h-birch", "Birch family")))
        #expect(vm.canDelete(Self.household("h-cedar", "Cedar family")))
    }

    @Test func aDeleteConflictSurfacesTheServersMessageVerbatim() async {
        let api = MockHouseholdsAPI()
        api.households = [Self.household("h-birch", "Birch family")]
        let detail = "You can't delete the household you belong to."
        api.deleteError = APIError.conflict(detail)
        let vm = HouseholdsViewModel(
            api: api, serverBaseURL: Self.serverBaseURL, currentHouseholdID: "h-cedar")
        await vm.load()

        await vm.delete(Self.household("h-birch", "Birch family"))

        #expect(vm.errorMessage == detail)
        #expect(api.deleted.isEmpty)
        #expect(api.listCalls == 1)  // no reload on failure
        #expect(vm.households.map(\.id) == ["h-birch"])
    }

    @Test func dismissInviteClearsTheLink() async {
        let api = MockHouseholdsAPI()
        api.createResponse = .init(
            household: Self.household("h-cedar", "Cedar family"),
            inviteToken: "one-time-secret",
            inviteExpiresAt: Date())
        let vm = makeViewModel(api)
        _ = await vm.create(
            displayName: "Cedar family", baseCurrency: "USD", ownerEmail: "cedar@example.test")
        #expect(vm.invite != nil)

        vm.dismissInvite()

        #expect(vm.invite == nil)
    }
}
