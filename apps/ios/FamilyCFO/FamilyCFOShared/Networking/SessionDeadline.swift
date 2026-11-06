import Foundation

/// The earlier of the server's wall-clock deadline and the lifetime observed
/// when this process installed the credential. A clock rollback adds no time.
struct SessionDeadline {
    let expiresAt: Date
    let monotonicExpiry: TimeInterval

    init(expiresAt: Date, wallNow: Date, monotonicNow: TimeInterval) {
        self.expiresAt = expiresAt
        monotonicExpiry = monotonicNow + expiresAt.timeIntervalSince(wallNow)
    }

    func remaining(wallNow: Date, monotonicNow: TimeInterval) -> TimeInterval {
        min(expiresAt.timeIntervalSince(wallNow), monotonicExpiry - monotonicNow)
    }

    private static let origin = ContinuousClock.now

    static func monotonicTime() -> TimeInterval {
        let elapsed = origin.duration(to: .now).components
        return Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18
    }
}
