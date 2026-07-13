import Foundation
import UserNotifications

/// Local notifications for upcoming bills (M92c). The bill list comes from the
/// existing household context (`upcoming_bills`); the reminders are scheduled and
/// fired entirely ON THE PHONE — no push infrastructure, nothing leaves the
/// LAN/tailnet. A bill due in `daysUntil` days is reminded the morning before.
///
/// The scheduling seam is protocol'd so the logic — which bills earn a reminder,
/// when, and de-duplication — is testable without the real notification center,
/// which needs an app running under the OS.
protocol NotificationScheduling: Sendable {
    func authorized() async -> Bool
    func pending() async -> Set<String>
    func schedule(id: String, title: String, body: String, at date: DateComponents) async
    func cancel(ids: [String]) async
}

struct BillReminder: Equatable {
    let id: String
    let title: String
    let body: String
    /// When the notification fires — the morning (9am) before the due date, or
    /// this morning if the bill is due today/tomorrow.
    let fireDate: DateComponents
}

enum BillNotificationPlanner {
    /// All reminder identifiers we own carry this prefix, so a refresh can cancel
    /// exactly our stale reminders without touching anything else.
    static let idPrefix = "bill-reminder."

    static func id(for bill: Components.Schemas.UpcomingBill) -> String {
        idPrefix + bill.id
    }

    /// One reminder per upcoming bill, fired the morning before it's due (bills
    /// already overdue or due today fire this morning). `now` is injectable for
    /// tests.
    static func reminders(
        for bills: [Components.Schemas.UpcomingBill],
        calendar: Calendar = .current,
        now: Date
    ) -> [BillReminder] {
        bills.map { bill in
            // daysUntil is the server's own count; remind one day earlier, but
            // never schedule in the past.
            let leadDays = max(0, bill.daysUntil - 1)
            let fire = calendar.date(byAdding: .day, value: leadDays, to: calendar.startOfDay(for: now))
                ?? now
            var components = calendar.dateComponents([.year, .month, .day], from: fire)
            components.hour = 9
            let dueText = bill.daysUntil <= 0 ? "is due today" : "is due in \(bill.daysUntil) day\(bill.daysUntil == 1 ? "" : "s")"
            return BillReminder(
                id: id(for: bill),
                title: "Upcoming bill",
                body: "\(bill.name) (\(bill.amount.formattedExact)) \(dueText).",
                fireDate: components
            )
        }
    }
}

/// Drives the planner against the real notification center. Called after the
/// household context loads (the Overview already fetches it), so reminders track
/// the latest bill data without any background polling of the box.
struct BillNotificationScheduler {
    let scheduler: NotificationScheduling

    func refresh(from bills: [Components.Schemas.UpcomingBill], now: Date = Date()) async {
        guard await scheduler.authorized() else { return }

        let reminders = BillNotificationPlanner.reminders(for: bills, now: now)
        let wanted = Set(reminders.map(\.id))

        // Drop reminders for bills that are gone (paid, deleted, or now further
        // out than the window), so the phone never nags about a bill that's off
        // the list.
        let existing = await scheduler.pending()
            .filter { $0.hasPrefix(BillNotificationPlanner.idPrefix) }
        let stale = existing.subtracting(wanted)
        if !stale.isEmpty {
            await scheduler.cancel(ids: Array(stale))
        }

        // Re-scheduling an existing identifier replaces it, so this is idempotent
        // — refreshing on every Overview load can't pile up duplicates.
        for reminder in reminders {
            await scheduler.schedule(
                id: reminder.id, title: reminder.title, body: reminder.body, at: reminder.fireDate)
        }
    }
}

/// The production `NotificationScheduling` backed by `UNUserNotificationCenter`.
struct SystemNotificationScheduler: NotificationScheduling {
    func authorized() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return true
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        default:
            return false
        }
    }

    func pending() async -> Set<String> {
        let requests = await UNUserNotificationCenter.current().pendingNotificationRequests()
        return Set(requests.map(\.identifier))
    }

    func schedule(id: String, title: String, body: String, at date: DateComponents) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let trigger = UNCalendarNotificationTrigger(dateMatching: date, repeats: false)
        let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)
        try? await UNUserNotificationCenter.current().add(request)
    }

    func cancel(ids: [String]) async {
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids)
    }
}
