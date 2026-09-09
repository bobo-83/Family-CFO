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

extension Components.Schemas.QualifiedMoney {
    /// The readable portion of this aggregate. A positive count is not a lower
    /// bound: an omitted inflow or refund can move the exact value either way.
    var amountMinor: Int64 { value.amountMinor }
    var currency: String { value.currency }
    var decimalValue: Decimal { value.decimalValue }
    var formatted: String { value.formatted }
    var formattedExact: String { value.formattedExact }
    var isPartial: Bool { incompleteCount > 0 }

    /// Visible and VoiceOver-safe qualification copy. This is a status note,
    /// never a transport alert and never a claim that the value is a minimum.
    var partialDisclosure: String? {
        guard incompleteCount > 0 else { return nil }
        if incompleteCount == 1 {
            return String(
                localized: "Partial total—1 stored amount could not be read and was left out.")
        }
        return String(
            localized: "Partial total—\(incompleteCount) stored amounts could not be read and were left out.")
    }

    var accessibilityDescription: String {
        guard let partialDisclosure else { return formatted }
        return "\(formatted). \(partialDisclosure)"
    }
}

extension Components.Schemas.ComputationAvailability {
    var isUnavailable: Bool { status == .unavailable }

    var unavailableDisclosure: String? {
        guard isUnavailable else { return nil }
        if incompleteCount == 1 {
            return String(
                localized: "Unavailable because 1 stored amount could not be read.")
        }
        return String(
            localized: "Unavailable because \(incompleteCount) stored amounts could not be read.")
    }
}

/// A single localized value used wherever a server decision is deliberately
/// null. Keeping it here prevents pages and complications from inventing a
/// positive/negative presentation for missing decisions.
var unavailableValueText: String { String(localized: "Unavailable") }
