import Foundation
import OpenAPIRuntime

/// Turns transport/client failures into actionable words. Shared so the phone
/// and the watch describe the same failure the same way (ADR 0067 v4).
enum AdvisorErrorDescriber {
    enum RequestContext: Equatable {
        case plainRequest
        case streamedTurn
    }

    static func describe(
        _ error: Error, during context: RequestContext = .plainRequest
    ) -> String {
        if let apiError = error as? APIError {
            return apiError.errorDescription ?? "\(apiError)"
        }
        // The generated client wraps transport failures; unwrap to say
        // precisely what went wrong instead of a catch-all guess.
        let nsError = SavedAnswerRecovery.transportError(error) as NSError
        guard nsError.domain == NSURLErrorDomain else {
            return String(localized: "Couldn't talk to your CFO: \(nsError.localizedDescription)")
        }
        switch nsError.code {
        case NSURLErrorCancelled:
            // Our pinning delegate cancels the challenge on a mismatch.
            return String(
                localized:
                    "The server's certificate doesn't match the pinned fingerprint from pairing. If the box's certificate changed, re-pair from the dashboard's Devices page."
            )
        case NSURLErrorTimedOut:
            if context == .streamedTurn {
                return String(
                    localized:
                        "The request timed out while the advisor was still working. The answer may still be saved — check this conversation again in a minute."
                )
            }
            return String(
                localized:
                    "The server didn't answer in time — it may be busy loading the model. Try again in a minute."
            )
        case NSURLErrorNetworkConnectionLost:
            if context == .streamedTurn {
                return String(
                    localized:
                        "The connection dropped while the advisor was still working. The answer may still be saved — check this conversation again in a minute."
                )
            }
            return unreachableMessage
        case NSURLErrorNotConnectedToInternet, NSURLErrorCannotConnectToHost,
            NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
            return unreachableMessage
        case NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateUntrusted,
            NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateNotYetValid:
            return String(
                localized:
                    "TLS handshake with the server failed (\(nsError.code)). If the box uses a self-signed certificate, re-pair so the app can pin the current one."
            )
        default:
            return String(localized: "Network error \(nsError.code): \(nsError.localizedDescription)")
        }
    }

    private static var unreachableMessage: String {
        String(
            localized:
                "Couldn't reach the server — check that this device is on the household network (or tailnet), and that Local Network access is allowed in Settings → Privacy & Security → Local Network."
        )
    }
}
