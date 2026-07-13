import Foundation

/// Builds an `OverviewSnapshot` (M92a) from the household context the Overview
/// already loaded. Kept in the app target — it depends on the generated client,
/// which the widget deliberately does not compile.
extension OverviewSnapshot {
    init(context: Components.Schemas.HouseholdContext, now: Date) {
        self.netWorthMinor = context.netWorth.amountMinor
        self.currency = context.netWorth.currency
        self.emergencyFundStatus = context.emergencyFund?.statusLabel ?? "—"
        self.emergencyFundMonths = context.emergencyFund?.months
        self.capturedAt = now
    }
}
