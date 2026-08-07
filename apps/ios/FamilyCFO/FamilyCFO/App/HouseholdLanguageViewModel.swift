import Foundation
import Observation

/// Backs the Settings Language row (#10 phase 1). Household-wide, not
/// per-member: one language per household is a server constraint (compile-time
/// web i18n serves one build per locale), so this only PATCHes the shared
/// setting and lets the context's next refresh carry it everywhere else.
@MainActor
@Observable
final class HouseholdLanguageViewModel {
    struct Option: Identifiable, Equatable {
        let code: String
        let name: String
        var id: String { code }
    }

    /// A language's own name is never translated, so these literals stay
    /// correct even before the UI itself is localized (later phases).
    static let options: [Option] = [
        Option(code: "en", name: "English"),
        Option(code: "vi", name: "Tiếng Việt"),
        Option(code: "lt", name: "Lietuvių"),
    ]

    private let api: HouseholdAPI

    private(set) var language = "en"
    var errorMessage: String?

    init(api: HouseholdAPI) {
        self.api = api
    }

    /// Best-effort read of the current value: a failed context fetch keeps the
    /// server default ("en") rather than blocking the whole Settings screen.
    func load() async {
        guard let context = try? await api.context(month: nil) else { return }
        language = context.language ?? "en"
    }

    func change(to code: String) async {
        guard code != language else { return }
        let previous = language
        language = code  // the Picker tracks us, so show the choice immediately
        do {
            try await api.updateLanguage(code)
            errorMessage = nil
        } catch {
            language = previous
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The read-only row for members without household.settings.manage.
    var displayName: String {
        Self.options.first { $0.code == language }?.name ?? language
    }
}
