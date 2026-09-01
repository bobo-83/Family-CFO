import Foundation
import Testing

@testable import FamilyCFO

/// ADR 0074 (amending ADR 0029): `/VERSION` holds a MAJOR.MINOR *contract*
/// shared by every component, and each component carries its own build integer
/// (`apps/{api,web,ios}/BUILD`), reporting `<contract>.<build>`. Two
/// deployables are compatible when their contracts match, whatever their builds
/// are — which is the whole point: before this, the app compared full version
/// strings for equality, so a backend-only patch made a byte-identical app show
/// the orange "install the update" banner.
///
/// The Swift side of that rule mirrors `contract_of`/`versions_compatible` in
/// scripts/lib/version.sh and `Shell.contractOf` in the dashboard.
@MainActor
struct VersionContractTests {
    @Test func theContractIsTheLeadingMajorMinor() {
        #expect(OverviewViewModel.contract(of: "0.157.4") == "0.157")
        #expect(OverviewViewModel.contract(of: "1.2.3") == "1.2")
    }

    /// Every product artifact must report the complete shape from ADR 0074.
    /// Invalid values are unknown rather than compatible with a valid prefix.
    @Test func malformedArtifactVersionsAreRejected() {
        for version in ["?", "", "0.157", "0.157.4.9", "0..157.bad", "0.157.corrupt", "0.157."] {
            #expect(OverviewViewModel.contract(of: version) == nil)
        }
    }

    /// The regression this whole issue exists for: `scripts/patch.sh api` bumps
    /// only `apps/api/BUILD`, and the phone must stay quiet about it.
    @Test func aBuildOnlyDifferenceIsNotAMismatch() {
        #expect(OverviewViewModel.versionsDiffer(app: "0.157.1", box: "0.157.4") == false)
        #expect(OverviewViewModel.versionsDiffer(app: "0.157.9", box: "0.157.0") == false)
    }

    /// A contract bump still means every component must ship — that case warns
    /// exactly as loudly as it did before ADR 0074.
    @Test func aContractDifferenceIsAMismatch() {
        #expect(OverviewViewModel.versionsDiffer(app: "0.157.4", box: "0.158.0") == true)
        #expect(OverviewViewModel.versionsDiffer(app: "0.157.0", box: "1.157.0") == true)
    }

    @Test func anInvalidVersionIsUnverifiableRatherThanCompatible() {
        #expect(OverviewViewModel.versionsDiffer(app: "0.157.1", box: "0.157.corrupt") == nil)
        #expect(OverviewViewModel.versionsDiffer(app: "0..157.bad", box: "0.157.1") == nil)
    }
}

/// The same rule as the view model actually applies it, driven through `load()`
/// so the guard and the real `appVersion` are both in the path. The box
/// versions are derived from this build's own contract rather than hardcoded —
/// the test bundle's MARKETING_VERSION is whatever the build stamped, so a
/// literal would only pass on the day it was written.
@MainActor
struct OverviewVersionMismatchTests {
    private func context() -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "demo-household",
            currency: "USD",
            netWorth: .init(amountMinor: 0, currency: "USD"),
            emergencyFundMonths: 0
        )
    }

    private func loaded(boxVersion: String?) async -> OverviewViewModel {
        let api = MockHouseholdAPI()
        api.context = context()
        api.version = boxVersion
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)
        await viewModel.load()
        return viewModel
    }

    /// A half-known pair is not a mismatch: an unreachable box leaves
    /// `serverVersion` nil, and calling that "stale" would put the banner up
    /// every time the phone is off the home network.
    @Test func anUnreachableBoxIsNotAMismatch() async {
        let viewModel = await loaded(boxVersion: nil)

        #expect(viewModel.serverVersion == nil)
        #expect(!viewModel.versionMismatch)
        #expect(!viewModel.versionUnverifiable)
    }

    @Test func aBoxOnTheSameContractShowsNoBanner() async throws {
        let contract = try #require(OverviewViewModel.contract(of: OverviewViewModel.appVersion))
        let viewModel = await loaded(boxVersion: "\(contract).999")

        #expect(viewModel.serverVersion == "\(contract).999")
        #expect(!viewModel.versionMismatch)
        #expect(!viewModel.versionUnverifiable)
    }

    @Test func aBoxOnAnotherContractShowsTheBanner() async {
        let viewModel = await loaded(boxVersion: "999999.0.0")

        #expect(viewModel.versionMismatch)
        #expect(!viewModel.versionUnverifiable)
    }

    @Test func aMalformedBoxVersionShowsTheUnverifiableWarning() async {
        let viewModel = await loaded(boxVersion: "0.157.corrupt")

        #expect(!viewModel.versionMismatch)
        #expect(viewModel.versionUnverifiable)
    }
}
