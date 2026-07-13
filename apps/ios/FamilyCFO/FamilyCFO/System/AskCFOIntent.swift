import AppIntents
import Foundation

/// "Ask my CFO …" via Siri and Shortcuts (M92b). The spoken question goes
/// through the SAME grounded chat pipeline as the in-app advisor — App Intents
/// is only a doorway, never a second brain (ADR 0018). The answer is returned as
/// dialog Siri reads back.
struct AskCFOIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask my CFO"
    static var description = IntentDescription(
        "Ask your household CFO a question and hear a grounded answer.")
    /// Answering needs the network and the paired credential, so don't try to
    /// run it purely in the background.
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Question", requestValueDialog: "What would you like to ask your CFO?")
    var question: String

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let answer = try await AskCFO.answer(question, using: AskCFO.resolveAPI())
        return .result(dialog: IntentDialog(stringLiteral: answer))
    }
}

/// The intent's logic, lifted out of the `AppIntent` shell so it's testable
/// without the AppIntents runtime (which only runs under the OS).
enum AskCFO {
    enum IntentError: Error, LocalizedError {
        case notPaired
        case emptyQuestion

        var errorDescription: String? {
            switch self {
            case .notPaired:
                return "Pair this phone with your household's box first, in the Family CFO app."
            case .emptyQuestion:
                return "I didn't catch a question."
            }
        }
    }

    /// Builds an API bound to the stored pairing, or throws if unpaired — the
    /// intent can run when the app isn't open, so it can't lean on `AppModel`.
    @MainActor
    static func resolveAPI() throws -> AdvisorAPI {
        let model = AppModel()
        model.bootstrap()
        guard let api = model.api else { throw IntentError.notPaired }
        return api
    }

    /// Sends the question through the grounded pipeline and returns a spoken-
    /// friendly answer (markdown stripped, as the voice loop does).
    static func answer(_ question: String, using api: AdvisorAPI) async throws -> String {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw IntentError.emptyQuestion }
        let response = try await api.sendMessage(trimmed, conversationID: nil, attachment: nil)
        return SpokenReply.speakable(response.recommendation.answer)
    }
}

/// Surfaces the intent to Siri/Spotlight with spoken trigger phrases.
///
/// Apple requires the app name in EVERY App Shortcut phrase, and Siri can't
/// reliably capture a free-form question from the same utterance — so the
/// working flow is "Ask Family CFO" (any phrase below), after which Siri prompts
/// for the question. The app's display name is "Family CFO" (two words) so Siri
/// hears it cleanly.
struct FamilyCFOShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskCFOIntent(),
            phrases: [
                "Ask \(.applicationName)",
                "Ask my CFO in \(.applicationName)",
                "Ask my advisor in \(.applicationName)",
                "\(.applicationName) how much money do I have",
                "Talk to \(.applicationName)",
            ],
            shortTitle: "Ask my CFO",
            systemImageName: "bubble.left.and.text.bubble.right"
        )
    }
}
