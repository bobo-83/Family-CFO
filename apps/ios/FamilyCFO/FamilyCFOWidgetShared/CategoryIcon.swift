import Foundation

/// SF Symbol for a category, matched on keywords in its name (moved out of
/// the phone's CategoryVisuals so the watch complications can label their
/// budget columns with the SAME icons — user request 2026-07-25). Everything
/// unmatched gets a neutral tag so every chip and column carries an icon.
enum CategoryIcon {
    static func symbol(for name: String) -> String {
        let n = name.lowercased()
        for (keywords, symbol) in iconRules where keywords.contains(where: n.contains) {
            return symbol
        }
        return "tag.fill"
    }

    static let iconRules: [([String], String)] = [
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
}
