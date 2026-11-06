import Foundation

/// Ordering metadata is persisted even after locally rejecting the bearer.
struct WatchContextOrder: Codable, Equatable {
    let scope: UUID
    let sequence: UInt64

    init?(_ context: [String: String]) {
        guard context["version"] == "1",
            let scope = context["scope"].flatMap(UUID.init(uuidString:)),
            let sequence = context["sequence"].flatMap(UInt64.init)
        else { return nil }
        self.scope = scope
        self.sequence = sequence
    }

    func accepts(_ next: Self, allowNewScope: Bool) -> Bool {
        next.scope == scope ? next.sequence > sequence : allowNewScope
    }
}
