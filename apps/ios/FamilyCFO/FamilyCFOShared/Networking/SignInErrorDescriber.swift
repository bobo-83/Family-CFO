import Foundation
import OpenAPIRuntime

/// Login/setup diagnostics expose only constant text and allowlisted URL error
/// codes. ClientError descriptions and NSError userInfo may carry credentials,
/// request bodies or URLs, so never interpolate them (issue #162, ADR 0079).
enum SignInErrorDescriber {
    static func describe(_ error: Error) -> String {
        var current = error
        var seen = Set<ObjectIdentifier>()
        var urlErrorCode: Int?
        for _ in 0..<16 {
            if let wrapper = current as? ClientError {
                current = wrapper.underlyingError
                continue
            }
            if current is CancellationError {
                return String(localized: "Sign-in was cancelled. Try again when ready.")
            }
            if current is DecodingError {
                return String(localized: "The server response could not be decoded. Check that the app and server are compatible.")
            }
            if current is EncodingError {
                return String(localized: "The sign-in request could not be encoded. Update the app and try again.")
            }
            let ns = current as NSError
            guard seen.insert(ObjectIdentifier(ns)).inserted else { break }
            // URLSession often nests a CFNetwork/SSL error beneath its useful
            // NSURLError. Keep that allowlisted code while walking deeper.
            if ns.domain == NSURLErrorDomain { urlErrorCode = ns.code }
            if let underlying = ns.userInfo[NSUnderlyingErrorKey] as? Error {
                current = underlying
                continue
            }
            break
        }
        if let code = urlErrorCode {
            let message: String
            switch code {
            case NSURLErrorCancelled, NSURLErrorUserCancelledAuthentication:
                message = String(localized: "Sign-in was cancelled. If you did not cancel it, verify the server certificate with the operator and use Change server to confirm it explicitly.")
            case NSURLErrorServerCertificateUntrusted, NSURLErrorSecureConnectionFailed,
                NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateNotYetValid,
                NSURLErrorServerCertificateHasUnknownRoot, NSURLErrorClientCertificateRejected,
                NSURLErrorClientCertificateRequired:
                message = String(localized: "The server certificate was rejected. Verify the server address and certificate with the operator. To trust a changed self-signed certificate, choose Change server and confirm it explicitly.")
            case NSURLErrorCannotConnectToHost:
                message = String(localized: "Nothing is listening at that address and port. The box serves 8443; check the port.")
            case NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
                message = String(localized: "That name could not be resolved. If it is a tailnet name, check the VPN is connected.")
            case NSURLErrorTimedOut:
                message = String(localized: "The server did not answer in time. Check its status and the network or VPN, then try again.")
            case NSURLErrorNotConnectedToInternet, NSURLErrorNetworkConnectionLost:
                message = String(localized: "This phone lost its network connection.")
            case NSURLErrorAppTransportSecurityRequiresSecureConnection:
                message = String(localized: "iOS blocked an insecure connection. Use the server’s HTTPS address.")
            default:
                return String(localized: "Sign-in failed unexpectedly. Check app/server compatibility and try again; the cause is not known.")
            }
            return "\(message)\n\n[NSURLErrorDomain \(code)]"
        }
        return String(localized: "Sign-in failed unexpectedly. Check app/server compatibility and try again; the cause is not known.")
    }
}
