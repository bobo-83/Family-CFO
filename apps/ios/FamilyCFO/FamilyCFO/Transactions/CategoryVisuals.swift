import Foundation

/// Icons and a merchant-aware recommendation for the category picker.
///
/// Known merchants never reach the picker — sync auto-files them from history —
/// so a history-based suggestion would always be empty here. The only offline
/// signal for a genuinely new merchant is its name, so the recommendation is a
/// keyword match from the merchant text to one of the household's own
/// categories, falling back to the most-used category the caller passes in.
enum CategoryVisuals {
    /// An SF Symbol for a category, matched on keywords in its name. Everything
    /// unmatched gets a neutral tag so every chip still carries an icon.
    static func icon(for name: String) -> String {
        let n = name.lowercased()
        for (keywords, symbol) in iconRules where keywords.contains(where: n.contains) {
            return symbol
        }
        return "tag.fill"
    }

    /// The category to pin at the top as "Recommended" for `merchant`. Returns a
    /// keyword match against the household's categories, else `fallback` (the
    /// most-used category), else nil when there are no categories at all.
    static func recommendation(
        merchant: String,
        categories: [Components.Schemas.Category],
        fallback: Components.Schemas.Category?
    ) -> Components.Schemas.Category? {
        let m = merchant.lowercased()
        for (keywords, categoryName) in merchantRules where keywords.contains(where: m.contains) {
            if let hit = categories.first(where: {
                $0.name.localizedCaseInsensitiveContains(categoryName)
            }) {
                return hit
            }
        }
        return fallback
    }

    // MARK: - Rules

    /// (keywords that may appear in a category name) → SF Symbol. First match wins,
    /// so order the more specific rules first.
    private static let iconRules: [([String], String)] = [
        (["grocer", "market"], "cart.fill"),
        (["food", "drink"], "cup.and.saucer.fill"),
        (["lunch", "dining", "restaurant", "coffee"], "fork.knife"),
        (["transport", "gas", "fuel", "auto", "car"], "car.fill"),
        (["travel", "flight", "vacation"], "airplane"),
        (["shop"], "bag.fill"),
        (["transfer"], "arrow.left.arrow.right"),
        (["income", "salary", "paycheck"], "dollarsign.circle.fill"),
        (["saving"], "banknote.fill"),
        (["tax"], "building.columns.fill"),
        (["hous", "rent", "mortgage"], "house.fill"),
        (["util", "electric", "water", "internet"], "bolt.fill"),
        (["insur"], "shield.fill"),
        (["health", "medical", "pharmacy", "doctor"], "cross.case.fill"),
        (["subscri"], "arrow.triangle.2.circlepath"),
        (["entertain", "movie", "music", "game"], "play.tv.fill"),
        (["tennis", "sport", "fitness", "gym", "racquet"], "figure.tennis"),
        (["kid", "child", "school", "daycare"], "figure.and.child.holdinghands"),
        (["parent", "family"], "figure.2"),
        (["gift", "donation", "charity"], "gift.fill"),
        (["pet"], "pawprint.fill"),
        (["other", "misc"], "square.grid.2x2.fill"),
    ]

    /// (keywords that may appear in a merchant name) → the category name to
    /// recommend. First match wins.
    private static let merchantRules: [([String], String)] = [
        (["grocer", "market", "whole foods", "trader joe", "safeway", "kroger",
          "aldi", "wegmans", "publix", "food store"], "Groceries"),
        (["starbucks", "coffee", "dunkin", "peet", "cafe", "tea"], "Dining"),
        (["restaurant", "grill", "pizza", "mcdonald", "chipotle", "taco",
          "sushi", "kitchen", "bbq", "bistro", "diner", "smoothie", "bar "], "Dining"),
        (["uber", "lyft", "shell", "chevron", "exxon", "mobil", "bp ", "gas",
          "fuel", "parking", "toll", "auto wash", "car wash"], "Transportation"),
        (["airline", "airways", "delta", "united", "jetblue", "flysas",
          "hotel", "airbnb", "marriott", "hilton", "expedia", "resort"], "Travel"),
        (["netflix", "spotify", "hulu", "disney+", "apple.com", "prime video",
          "subscription", "youtube premium"], "Subscriptions"),
        (["pharmacy", "cvs", "walgreens", "clinic", "hospital", "medical",
          "dental", "doctor", "health"], "Health"),
        (["electric", "water dept", "utility", "comcast", "xfinity", "verizon",
          "at&t", "internet"], "Utilities"),
        (["geico", "allstate", "state farm", "progressive", "insurance"], "Insurance"),
        (["rent", "mortgage", "hoa", "property mgmt"], "Housing"),
        (["racquet", "tennis", "gym", "fitness", "golf"], "Tennis"),
        (["sporting", "sports", "nike", "adidas", "rei", "lululemon",
          "foot locker", "dick's"], "Shopping"),
        (["amazon", "walmart", "target", "costco", "best buy", "macy",
          "nordstrom", "madewell", "store", "mall"], "Shopping"),
    ]
}
