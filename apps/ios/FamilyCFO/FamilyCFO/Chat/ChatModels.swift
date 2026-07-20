import Foundation

/// A message row in the chat transcript — either loaded from conversation
/// history or produced live in this session.
struct ChatMessage: Identifiable, Equatable {
    enum Author: Equatable {
        case user
        case assistant
    }

    let id: String
    let author: Author
    let text: String
    /// Grounded metadata, present on live assistant answers (history rows
    /// only carry text).
    var confidence: Double?
    var warnings: [String] = []
    var impacts: [Components.Schemas.Impact] = []
    var attachmentName: String?
    /// The rated answer's recommendation id, so 👍/👎 works on live AND history
    /// assistant messages (ADR 0044). nil for user rows.
    var recommendationId: String?
    /// This member's rating of the answer, once given.
    var rating: Components.Schemas.AdvisorFeedbackRequest.RatingPayload?

    static func from(_ message: Components.Schemas.ConversationMessage) -> ChatMessage {
        ChatMessage(
            id: message.id,
            author: message.role == .user ? .user : .assistant,
            text: message.content,
            recommendationId: message.recommendationId
        )
    }

    static func from(_ recommendation: Components.Schemas.Recommendation) -> ChatMessage {
        ChatMessage(
            id: recommendation.id,
            author: .assistant,
            text: recommendation.answer,
            confidence: recommendation.confidence,
            warnings: recommendation.warnings ?? [],
            impacts: recommendation.impacts,
            recommendationId: recommendation.id
        )
    }
}
