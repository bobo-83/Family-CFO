import Foundation

/// Drives the Advisor-knowledge screen (ADR 0040).
@MainActor
@Observable
final class AiStudyViewModel {
    private let api: AiStudyAPI

    private(set) var status: Components.Schemas.AiStudyStatus?
    private(set) var isLoading = false
    var errorMessage: String?

    init(api: AiStudyAPI) { self.api = api }

    var coverageFraction: Double {
        guard let status else { return 0 }
        return Double(status.coveragePercent) / 100
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            status = try await api.status()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
