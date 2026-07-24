import Foundation

extension Components.Schemas.Money {
    /// Minor units are the contract's storage (M2) — never format them raw.
    var decimalValue: Decimal {
        Decimal(amountMinor) / 100
    }

    var formatted: String {
        decimalValue.formatted(.currency(code: currency).precision(.fractionLength(0)))
    }

    /// Two-decimal form, for amounts where the cents carry meaning (a single
    /// bill) rather than adding noise (net worth).
    var formattedExact: String {
        decimalValue.formatted(.currency(code: currency))
    }
}
