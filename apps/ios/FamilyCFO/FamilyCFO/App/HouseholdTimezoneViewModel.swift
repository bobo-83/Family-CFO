import Foundation
import Observation

/// Backs the Settings Time zone row (#41). Household-wide, not per-member: the
/// server reckons what "today" means — bills due-soon, overdue flips, safe to
/// spend's horizon — from this one column, so every member has to see the same
/// date. Only `household.settings.manage` may change it, the right the PATCH
/// checks, exactly like the Language row (#10).
@MainActor
@Observable
final class HouseholdTimezoneViewModel {
    /// Reachable without typing anything. The device knows hundreds of zones —
    /// one flat list of them is a wall, not a choice — so the rest lives behind
    /// the search field. Zone IDs are identifiers and are never translated.
    static let curated: [String] = [
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Toronto",
        "Europe/London",
        "Europe/Dublin",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Vilnius",
        "Asia/Ho_Chi_Minh",
        "Asia/Singapore",
        "Asia/Tokyo",
        "Australia/Sydney",
        "UTC",
    ]

    /// Everything searchable: what this device knows, plus the curated set —
    /// `knownTimeZoneIdentifiers` omits aliases like UTC on some releases, and
    /// a shortlist entry that search cannot find would be a trap.
    static let allZones: [String] = {
        var seen = Set<String>()
        return (TimeZone.knownTimeZoneIdentifiers + curated)
            .filter { seen.insert($0).inserted }
            .sorted()
    }()

    private let api: HouseholdAPI

    /// nil = never chosen; dates follow the box's own zone.
    private(set) var timezone: String?
    var errorMessage: String?

    init(api: HouseholdAPI) {
        self.api = api
    }

    /// Best-effort read of the current value: a failed context fetch leaves the
    /// row on "not set" rather than blocking the whole Settings screen.
    func load() async {
        guard let context = try? await api.context(month: nil) else { return }
        timezone = context.timezone
    }

    func change(to identifier: String) async {
        guard identifier != timezone else { return }
        let previous = timezone
        timezone = identifier  // the row tracks us, so show the choice immediately
        do {
            try await api.updateTimezone(identifier)
            errorMessage = nil
        } catch {
            timezone = previous
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The row's value — for readers, and for the picker's closed state.
    var displayName: String {
        timezone ?? String(localized: "Not set — the box's own zone")
    }

    /// What the picker lists: until something is typed, a shortlist led by the
    /// household's own zone (so a zone outside the curated set still shows as
    /// chosen) and then this phone's, which is nearly always what's meant.
    /// After that, every zone, searched by substring.
    func options(matching query: String) -> [String] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .replacingOccurrences(of: " ", with: "_")  // "new york" finds New_York
        guard !needle.isEmpty else { return shortlist }
        return Self.allZones.filter { $0.lowercased().contains(needle) }
    }

    private var shortlist: [String] {
        var seen = Set<String>()
        return ([timezone, TimeZone.current.identifier] + Self.curated)
            .compactMap { $0 }
            .filter { seen.insert($0).inserted }
    }
}
