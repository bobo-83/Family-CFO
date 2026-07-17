import Foundation
import UserNotifications

/// Fires a local notification when the most recent backup failed — either the
/// backup itself or its copy to the Synology (M98). Checked on app launch (from
/// the tab shell), deduped per backup id so the same failure never nags twice.
/// Local-only, like the bill reminders — nothing leaves the LAN.
struct BackupFailureNotifier {
    let api: BackupAPI
    private static let notifiedKey = "backup.failure.notified.ids"

    func check(now: Date = Date()) async {
        guard let latest = try? await api.config().latest else { return }
        let backupFailed = latest.status == .failed
        let remoteFailed = latest.remoteStatus == "failed"
        guard backupFailed || remoteFailed else { return }

        var notified = Set(UserDefaults.standard.stringArray(forKey: Self.notifiedKey) ?? [])
        guard !notified.contains(latest.id) else { return }

        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        let authorized: Bool
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: authorized = true
        case .notDetermined:
            authorized = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        default: authorized = false
        }
        guard authorized else { return }

        let content = UNMutableNotificationContent()
        content.title = "Backup problem"
        content.body =
            backupFailed
            ? "Last backup failed" + (latest.errorMessage.map { ": \($0)" } ?? ".")
            : "Backup couldn't reach your Synology"
                + (latest.remoteError.map { ": \($0)" } ?? ".")
        content.sound = .default
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 2, repeats: false)
        let request = UNNotificationRequest(
            identifier: "backup-failure.\(latest.id)", content: content, trigger: trigger)
        try? await center.add(request)

        notified.insert(latest.id)
        // Keep the set small — only the recent handful matters for dedup.
        UserDefaults.standard.set(Array(notified.suffix(20)), forKey: Self.notifiedKey)
    }
}
