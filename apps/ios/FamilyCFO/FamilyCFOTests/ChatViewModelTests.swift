import Foundation
import Testing

@testable import FamilyCFO

/// Scripted stand-in for the generated client.
final class MockAdvisorAPI: AdvisorAPI, @unchecked Sendable {
    var conversations: [Components.Schemas.Conversation] = []
    var detail: Components.Schemas.ConversationDetail?
    var response: Components.Schemas.ChatResponse?
    var error: Error?
    private(set) var sentMessages: [(message: String, conversationID: String?, attachment: ChatAttachment?)] = []

    func listConversations() async throws -> [Components.Schemas.Conversation] {
        if let error { throw error }
        return conversations
    }

    func conversation(id: String) async throws -> Components.Schemas.ConversationDetail {
        if let error { throw error }
        return detail!
    }

    var deleteError: Error?
    private(set) var deleted: [String] = []

    func deleteConversation(id: String) async throws {
        if let deleteError { throw deleteError }
        deleted.append(id)
        conversations.removeAll { $0.id == id }
    }

    var feedbackError: Error?
    private(set) var feedback:
        [(String, Components.Schemas.AdvisorFeedbackRequest.RatingPayload, String?)] = []

    func submitFeedback(
        recommendationId: String,
        rating: Components.Schemas.AdvisorFeedbackRequest.RatingPayload,
        note: String?
    ) async throws {
        if let feedbackError { throw feedbackError }
        feedback.append((recommendationId, rating, note))
    }

    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?
    ) async throws -> Components.Schemas.ChatResponse {
        // Behave like URLSession: a cancelled task aborts the request.
        try Task.checkCancellation()
        sentMessages.append((message, conversationID, attachment))
        if let error { throw error }
        return response!
    }
}

@MainActor
struct ChatViewModelTests {
    private func recommendation(answer: String) -> Components.Schemas.Recommendation {
        .init(
            id: "rec-1",
            answer: answer,
            assumptions: [],
            impacts: [],
            tradeoffs: [],
            alternatives: [],
            confidence: 0.9,
            calculationRefs: [],
            warnings: ["Numbers are month-to-date."]
        )
    }

    @Test func sendAppendsUserAndGroundedAssistantMessages() async {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "conv-1", recommendation: recommendation(answer: "You can afford it."))
        let viewModel = ChatViewModel(api: api)

        await viewModel.send("  Can we afford a new laptop?  ")

        #expect(api.sentMessages.count == 1)
        #expect(api.sentMessages[0].message == "Can we afford a new laptop?")
        #expect(viewModel.messages.count == 2)
        #expect(viewModel.messages[0].author == .user)
        #expect(viewModel.messages[1].author == .assistant)
        #expect(viewModel.messages[1].text == "You can afford it.")
        #expect(viewModel.messages[1].confidence == 0.9)
        #expect(viewModel.messages[1].warnings == ["Numbers are month-to-date."])
        // The conversation id from the response threads follow-up messages.
        #expect(viewModel.conversationID == "conv-1")
    }

    @Test func sendCarriesThePendingAttachmentExactlyOnce() async {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "conv-1", recommendation: recommendation(answer: "Looks like a receipt."))
        let viewModel = ChatViewModel(api: api)
        viewModel.pendingAttachment = ChatAttachment(
            data: Data([0xFF, 0xD8, 0xFF, 0x00]), kind: .visual(.imageJpeg), displayName: "Photo")

        await viewModel.send("What's this?")
        await viewModel.send("And without attachment?")

        #expect(api.sentMessages[0].attachment != nil)
        #expect(api.sentMessages[1].attachment == nil)
    }

    @Test func failuresKeepTheUsersMessageAndSurfaceTheError() async {
        let api = MockAdvisorAPI()
        api.error = APIError.server(500)
        let viewModel = ChatViewModel(api: api)

        await viewModel.send("hello")

        #expect(viewModel.messages.count == 1)
        #expect(viewModel.messages[0].author == .user)
        #expect(viewModel.errorMessage != nil)
    }

    @Test func unauthorizedGetsTheRePairMessage() async {
        let api = MockAdvisorAPI()
        api.error = APIError.unauthorized
        let viewModel = ChatViewModel(api: api)

        await viewModel.send("hello")

        #expect(viewModel.errorMessage?.contains("Re-pair") == true)
    }

    @Test func loadHistoryOrdersBySequence() async {
        let api = MockAdvisorAPI()
        api.detail = .init(
            id: "conv-1",
            title: "Laptop",
            createdAt: .now,
            updatedAt: .now,
            messages: [
                .init(id: "m2", role: .assistant, content: "Yes.", sequence: 2, createdAt: .now),
                .init(id: "m1", role: .user, content: "Can we?", sequence: 1, createdAt: .now),
            ]
        )
        let viewModel = ChatViewModel(api: api, conversationID: "conv-1")

        await viewModel.loadHistory()

        #expect(viewModel.messages.map(\.id) == ["m1", "m2"])
        #expect(viewModel.messages[0].author == .user)
        #expect(viewModel.messages[1].author == .assistant)
    }
}

@MainActor
struct VoiceConversationAdoptionTests {
    /// The reported bug (2026-07-13): a hands-free session runs through the same
    /// grounded pipeline, so the box DOES create a conversation — but the id came
    /// back to the voice view model and died with it, so the thread existed on the
    /// server and the app never showed it.
    @Test func adoptingAVoiceConversationLoadsItsTurns() async {
        let api = MockAdvisorAPI()
        api.detail = .init(
            id: "conv-voice",
            title: "How much can I spend?",
            createdAt: Date(),
            updatedAt: Date(),
            messages: [
                .init(
                    id: "m1", role: .user, content: "How much can I spend?", sequence: 1,
                    createdAt: .now),
                .init(
                    id: "m2", role: .assistant, content: "$1,792.00 is safe to spend.",
                    sequence: 2, createdAt: .now),
            ]
        )
        let viewModel = ChatViewModel(api: api)
        #expect(viewModel.conversationID == nil)

        await viewModel.adopt(conversationID: "conv-voice")

        #expect(viewModel.conversationID == "conv-voice")
        #expect(viewModel.messages.count == 2)
        #expect(viewModel.messages.first?.text == "How much can I spend?")
    }

    @Test func adoptingTheConversationAlreadyOpenIsANoOp() async {
        let api = MockAdvisorAPI()
        let viewModel = ChatViewModel(api: api, conversationID: "conv-1")

        await viewModel.adopt(conversationID: "conv-1")

        #expect(viewModel.conversationID == "conv-1")
    }

    @Test func ratingAnAnswerSubmitsFeedbackAndMarksItLocally() async {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "conv-1",
            recommendation: .init(
                id: "rec-1", answer: "You can afford it.", assumptions: [], impacts: [],
                tradeoffs: [], alternatives: [], confidence: 0.9, calculationRefs: [], warnings: []))
        let viewModel = ChatViewModel(api: api)
        await viewModel.send("Can I afford it?")
        let answer = viewModel.messages.last!

        await viewModel.rate(answer, .up)

        #expect(api.feedback.map(\.0) == ["rec-1"])
        #expect(api.feedback.first?.1 == .up)
        #expect(api.feedback.first?.2 == nil)  // no note on a plain rating
        #expect(viewModel.messages.last?.rating == .up)
    }

    @Test func aDownvoteNoteIsSentAndBlankNotesAreDropped() async {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "conv-1",
            recommendation: .init(
                id: "rec-1", answer: "You can afford it.", assumptions: [], impacts: [],
                tradeoffs: [], alternatives: [], confidence: 0.9, calculationRefs: [], warnings: []))
        let viewModel = ChatViewModel(api: api)
        await viewModel.send("Can I afford it?")
        let answer = viewModel.messages.last!

        await viewModel.rate(answer, .down, note: "  you ignored my RSUs  ")
        await viewModel.rate(answer, .down, note: "   ")  // whitespace-only → nil

        #expect(api.feedback.map(\.2) == ["you ignored my RSUs", nil])
    }

    @Test func aFailedRatingRevertsAndSurfacesTheError() async {
        let api = MockAdvisorAPI()
        api.response = .init(
            conversationId: "conv-1",
            recommendation: .init(
                id: "rec-1", answer: "You can afford it.", assumptions: [], impacts: [],
                tradeoffs: [], alternatives: [], confidence: 0.9, calculationRefs: [], warnings: []))
        let viewModel = ChatViewModel(api: api)
        await viewModel.send("Can I afford it?")
        api.feedbackError = APIError.server(500)

        await viewModel.rate(viewModel.messages.last!, .down)

        #expect(viewModel.messages.last?.rating == nil)  // reverted
        #expect(viewModel.errorMessage != nil)
    }
}
