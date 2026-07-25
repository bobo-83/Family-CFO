import Foundation
import OpenAPIRuntime

/// Turns transport/client failures into actionable words. Shared so the phone
/// and the watch describe the same failure the same way (ADR 0067 v4).
enum AdvisorErrorDescriber {
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
            return "Couldn't reach the server — check that this device is on the household network (or tailnet), and that Local Network access is allowed in Settings → Privacy & Security → Local Network."
        case NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateUntrusted,
            NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateNotYetValid:
            return "TLS handshake with the server failed (\(nsError.code)). If the box uses a self-signed certificate, re-pair so the app can pin the current one."
        default:
            return "Network error \(nsError.code): \(nsError.localizedDescription)"
        }
    }
}
