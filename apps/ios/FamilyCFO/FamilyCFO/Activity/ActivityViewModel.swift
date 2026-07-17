import Foundation

/// Drives the Activity/History screen (M101).
@MainActor
@Observable
final class ActivityViewModel {
    private let api: ActivityAPI

    private(set) var events: [Components.Schemas.AuditEvent] = []
    private(set) var isLoading = false
    private(set) var undoingID: String?
    var errorMessage: String?

    init(api: ActivityAPI) { self.api = api }

    var isEmpty: Bool { events.isEmpty }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            events = try await api.events()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func undo(_ event: Components.Schemas.AuditEvent) async {
        guard undoingID == nil else { return }
        undoingID = event.id
        defer { undoingID = nil }
        do {
            let updated = try await api.undo(eventID: event.id)
            if let index = events.firstIndex(where: { $0.id == updated.id }) {
                events[index] = updated
            }
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
