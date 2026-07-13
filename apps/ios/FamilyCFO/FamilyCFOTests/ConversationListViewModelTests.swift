import Foundation
import Testing

@testable import FamilyCFO

@MainActor
struct ConversationListViewModelTests {
    private func conversation(_ id: String, _ title: String) -> Components.Schemas.Conversation {
        .init(id: id, title: title, createdAt: Date(), updatedAt: Date())
    }

    private func loaded() -> (ConversationListViewModel, MockAdvisorAPI) {
        let api = MockAdvisorAPI()
        api.conversations = [
            conversation("a", "How much money do I have?"),
            conversation("b", "Can I afford this happy meal"),
            conversation("c", "Should I go to Copenhagen"),
        ]
        return (ConversationListViewModel(api: api), api)
    }

    @Test func deletingRemovesTheThreadFromTheListAndTheBox() async {
        let (viewModel, api) = loaded()
        await viewModel.load()

        await viewModel.delete(id: "b")

        #expect(api.deleted == ["b"])
        #expect(viewModel.conversations.map(\.id) == ["a", "c"])
        #expect(viewModel.errorMessage == nil)
    }

    /// A row that vanishes while the box still has the thread is a lie — the
    /// user would believe it was deleted. On failure the row comes back.
    @Test func aFailedDeleteRestoresTheRowAndSaysSo() async {
        let (viewModel, api) = loaded()
        await viewModel.load()
        api.deleteError = APIError.server(500)

        await viewModel.delete(id: "b")

        #expect(viewModel.conversations.map(\.id) == ["a", "b", "c"])
        #expect(viewModel.errorMessage != nil)
    }

    @Test func deletingRestoresTheRowInItsOriginalPosition() async {
        let (viewModel, api) = loaded()
        await viewModel.load()
        api.deleteError = APIError.unauthorized

        await viewModel.delete(id: "a")

        #expect(viewModel.conversations.first?.id == "a")
    }

    @Test func deletingAnUnknownThreadIsANoOp() async {
        let (viewModel, api) = loaded()
        await viewModel.load()

        await viewModel.delete(id: "nope")

        #expect(api.deleted.isEmpty)
        #expect(viewModel.conversations.count == 3)
    }
}
