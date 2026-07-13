import Foundation
import Observation
import OpenAPIRuntime

@MainActor
@Observable
final class ChatViewModel {
    let api: AdvisorAPI
    private(set) var conversationID: String?
    private(set) var messages: [ChatMessage] = []
    private(set) var isSending = false
    private(set) var isLoadingHistory = false
    var errorMessage: String?
    var pendingAttachment: ChatAttachment?

    init(api: AdvisorAPI, conversationID: String? = nil) {
        self.api = api
        self.conversationID = conversationID
    }

    func loadHistory() async {
        guard let conversationID, messages.isEmpty else { return }
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            let detail = try await api.conversation(id: conversationID)
            messages = detail.messages
                .sorted { $0.sequence < $1.sequence }
                .map(ChatMessage.from)
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    func send(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isSending else { return }

        let attachment = pendingAttachment
        pendingAttachment = nil
        var outgoing = ChatMessage(
            id: "local-\(messages.count)-\(trimmed.hashValue)",
            author: .user,
            text: trimmed
        )
        outgoing.attachmentName = attachment?.displayName
        messages.append(outgoing)
        isSending = true
        defer { isSending = false }

        do {
            let response = try await api.sendMessage(
                trimmed,
                conversationID: conversationID,
                attachment: attachment
            )
            conversationID = response.conversationId
            messages.append(.from(response.recommendation))
            errorMessage = nil
        } catch {
            // Keep the user's message visible; surface the failure and let
            // them retry by sending again.
            errorMessage = Self.describe(error)
        }
    }

    static func describe(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.errorDescription ?? "\(apiError)"
        }
        // The generated client wraps transport failures; unwrap to say
        // precisely what went wrong instead of a catch-all guess.
        let underlying = (error as? ClientError)?.underlyingError ?? error
        let nsError = underlying as NSError
        guard nsError.domain == NSURLErrorDomain else {
            return "Couldn't talk to your CFO: \(nsError.localizedDescription)"
        }
        switch nsError.code {
        case NSURLErrorCancelled:
            // Our pinning delegate cancels the challenge on a mismatch.
            return "The server's certificate doesn't match the pinned fingerprint from pairing. If the box's certificate changed, re-pair from the dashboard's Devices page."
        case NSURLErrorTimedOut:
            return "The server didn't answer in time — it may be busy loading the model. Try again in a minute."
        case NSURLErrorNotConnectedToInternet, NSURLErrorNetworkConnectionLost,
            NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
            return "Couldn't reach the server — check that this phone is on the household network (or tailnet), and that Local Network access is allowed in Settings → Privacy & Security → Local Network."
        case NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateUntrusted,
            NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateNotYetValid:
            return "TLS handshake with the server failed (\(nsError.code)). If the box uses a self-signed certificate, re-pair so the app can pin the current one."
        default:
            return "Network error \(nsError.code): \(nsError.localizedDescription)"
        }
    }
}
