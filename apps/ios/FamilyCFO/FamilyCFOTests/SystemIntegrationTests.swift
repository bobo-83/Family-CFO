import Foundation
import Testing

@testable import FamilyCFO

struct BillNotificationPlannerTests {
    private func bill(_ id: String, _ name: String, daysUntil: Int) -> Components.Schemas.UpcomingBill {
        .init(
            id: id,
            name: name,
            amount: .init(amountMinor: 3_299, currency: "USD"),
            dueDate: "2026-08-01",
            daysUntil: daysUntil
        )
    }

    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "America/New_York")!
        return c
    }

    private var now: Date {
        // 2026-07-13 08:00 ET
        calendar.date(from: DateComponents(year: 2026, month: 7, day: 13, hour: 8))!
    }

    @Test func remindsTheMorningBeforeADueBill() {
        let reminders = BillNotificationPlanner.reminders(
            for: [bill("b1", "Internet", daysUntil: 5)], calendar: calendar, now: now)

        #expect(reminders.count == 1)
        // Due in 5 days -> remind 4 days out, at 9am.
        #expect(reminders[0].fireDate.day == 17)
        #expect(reminders[0].fireDate.hour == 9)
        #expect(reminders[0].body.contains("Internet"))
        #expect(reminders[0].body.contains("$32.99"))
    }

    @Test func aBillDueTodayFiresThisMorningNotInThePast() {
        let reminders = BillNotificationPlanner.reminders(
            for: [bill("b1", "Rent", daysUntil: 0)], calendar: calendar, now: now)

        #expect(reminders[0].fireDate.day == 13)  // today, not day -1
        #expect(reminders[0].body.contains("due today"))
    }

    @Test func reminderIdsAreStablePerBillSoRefreshIsIdempotent() {
        let b = bill("bill-42", "Gym", daysUntil: 3)

        #expect(BillNotificationPlanner.id(for: b) == "bill-reminder.bill-42")
    }
}

/// Fake notification center, so the scheduler's refresh logic — cancel stale,
/// (re)schedule wanted — is tested without the OS.
actor FakeNotificationScheduler: NotificationScheduling {
    var isAuthorized: Bool
    private(set) var scheduled: [String: String] = [:]  // id -> body
    private(set) var cancelled: [String] = []

    init(authorized: Bool = true, existing: [String] = []) {
        isAuthorized = authorized
        for id in existing { scheduled[id] = "old" }
    }

    func authorized() async -> Bool { isAuthorized }
    func pending() async -> Set<String> { Set(scheduled.keys) }
    func schedule(id: String, title: String, body: String, at date: DateComponents) async {
        scheduled[id] = body
    }
    func cancel(ids: [String]) async {
        cancelled.append(contentsOf: ids)
        for id in ids { scheduled[id] = nil }
    }
}

struct BillNotificationSchedulerTests {
    private func bill(_ id: String, daysUntil: Int) -> Components.Schemas.UpcomingBill {
        .init(
            id: id, name: "Bill \(id)",
            amount: .init(amountMinor: 1000, currency: "USD"),
            dueDate: "2026-08-01", daysUntil: daysUntil)
    }

    @Test func schedulesOnePerBill() async {
        let fake = FakeNotificationScheduler()
        let scheduler = BillNotificationScheduler(scheduler: fake)

        await scheduler.refresh(from: [bill("a", daysUntil: 2), bill("b", daysUntil: 5)])

        let pending = await fake.pending()
        #expect(pending == ["bill-reminder.a", "bill-reminder.b"])
    }

    /// A bill that dropped off the list (paid, deleted) must have its reminder
    /// cancelled — the phone must never nag about a bill that's gone.
    @Test func cancelsRemindersForBillsNoLongerInTheList() async {
        let fake = FakeNotificationScheduler(existing: ["bill-reminder.gone", "bill-reminder.a"])
        let scheduler = BillNotificationScheduler(scheduler: fake)

        await scheduler.refresh(from: [bill("a", daysUntil: 2)])

        let cancelled = await fake.cancelled
        #expect(cancelled == ["bill-reminder.gone"])
    }

    @Test func doesNothingWithoutNotificationPermission() async {
        let fake = FakeNotificationScheduler(authorized: false)
        let scheduler = BillNotificationScheduler(scheduler: fake)

        await scheduler.refresh(from: [bill("a", daysUntil: 2)])

        let pending = await fake.pending()
        #expect(pending.isEmpty)
    }

    /// It must not touch notifications that aren't ours, even when cancelling.
    @Test func leavesForeignPendingNotificationsAlone() async {
        let fake = FakeNotificationScheduler(existing: ["some-other-app-thing"])
        let scheduler = BillNotificationScheduler(scheduler: fake)

        await scheduler.refresh(from: [bill("a", daysUntil: 2)])

        let cancelled = await fake.cancelled
        #expect(cancelled.isEmpty)  // the foreign id was never a candidate
    }
}

@MainActor
struct AskCFOIntentTests {
    @Test func routesTheQuestionThroughTheGroundedPipelineAndSpeaksTheAnswer() async throws {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "c1",
            recommendation: .init(
                id: "r1", answer: "You have **$6,765.83** safe to spend.",
                assumptions: [], impacts: [], tradeoffs: [], alternatives: [],
                confidence: 0.9, calculationRefs: [], warnings: []))

        let answer = try await AskCFO.answer("How much can I spend?", using: api)

        #expect(api.sentMessages.first?.message == "How much can I spend?")
        // Markdown stripped, as Siri should hear it.
        #expect(answer == "You have $6,765.83 safe to spend.")
    }

    @Test func anEmptyQuestionIsRejectedBeforeHittingTheServer() async {
        let api = MockAdvisorAPI()

        await #expect(throws: AskCFO.IntentError.self) {
            try await AskCFO.answer("   ", using: api)
        }
        #expect(api.sentMessages.isEmpty)
    }
}

struct OverviewSnapshotStoreTests {
    // A unique suite per test so they don't collide in the shared defaults.
    private func store(_ suite: String) -> OverviewSnapshotStore {
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        return OverviewSnapshotStore(suiteName: suite)
    }

    private func snapshot(_ minor: Int64) -> OverviewSnapshot {
        OverviewSnapshot(
            netWorthMinor: minor, currency: "USD",
            emergencyFundStatus: "On track", emergencyFundMonths: 4.5,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000))
    }

    @Test func savesAndLoadsRoundTrip() {
        let s = store("test.suite.roundtrip")
        s.save(snapshot(1_234_500))

        let loaded = s.load()
        #expect(loaded?.netWorthMinor == 1_234_500)
        #expect(loaded?.emergencyFundStatus == "On track")
    }

    @Test func loadReturnsNilWhenNothingSaved() {
        let s = store("test.suite.empty")
        #expect(s.load() == nil)
    }

    @Test func netWorthFormatsFromMinorUnits() {
        #expect(snapshot(1_234_500).netWorthFormatted == "$12,345")
    }
}
