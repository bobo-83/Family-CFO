import Foundation

/// A sensible starter set of spending categories (M91a), offered one-tap when a
/// household has none yet. Deliberately broad and few — the family renames,
/// splits, or deletes on the dashboard; this just gets the phone usable fast.
enum CategoryDefaults {
    static let starter: [String] = [
        "Groceries",
        "Dining",
        "Transportation",
        "Shopping",
        "Utilities",
        "Housing",
        "Entertainment",
        "Health",
        "Travel",
        "Subscriptions",
        "Income",
        "Transfers",
        "Savings",
        "Other",
    ]
}
