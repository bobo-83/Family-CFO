import Foundation

/// The JSON carried by the dashboard's pairing QR code (M83a).
///
/// Produced by `POST /pairing/sessions` on the server — see
/// `apps/api/src/family_cfo_api/api/pairing.py`. The payload is the trust
/// root for the whole pairing flow: it carries the API base URL, the one-time
/// pairing secret, and the TLS certificate fingerprint the app pins.
struct PairingPayload: Equatable {
    static let expectedType = "family-cfo-pairing"
    static let supportedVersion = 1

    let apiBaseURL: URL
    let pairingSessionID: String
    let householdID: String
    let householdName: String
    let expiresAt: Date?
    /// SHA-256 of the server's DER certificate (lowercase hex); nil when the
    /// server could not read its own certificate. Without it the app falls
    /// back to system TLS trust, which rejects self-signed certificates.
    let certificateSHA256: String?

    enum ParseError: Error, Equatable {
        case notJSON
        case wrongType
        case unsupportedVersion(Int)
        case missingField(String)
        case invalidBaseURL(String)
    }

    /// Parses a scanned QR string. Strict about identity fields, lenient
    /// about extras so older apps keep pairing against newer servers.
    static func parse(_ raw: String) throws -> PairingPayload {
        guard let data = raw.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data),
            let json = object as? [String: Any]
        else {
            throw ParseError.notJSON
        }
        guard json["type"] as? String == expectedType else {
            throw ParseError.wrongType
        }
        let version = json["version"] as? Int ?? -1
        guard version == supportedVersion else {
            throw ParseError.unsupportedVersion(version)
        }
        guard let rawURL = json["api_base_url"] as? String else {
            throw ParseError.missingField("api_base_url")
        }
        guard let url = URL(string: rawURL), let scheme = url.scheme,
            ["https", "http"].contains(scheme.lowercased())
        else {
            throw ParseError.invalidBaseURL(rawURL)
        }
        guard let sessionID = json["pairing_session_id"] as? String, !sessionID.isEmpty else {
            throw ParseError.missingField("pairing_session_id")
        }
        guard let householdID = json["household_id"] as? String else {
            throw ParseError.missingField("household_id")
        }
        guard let householdName = json["household_name"] as? String else {
            throw ParseError.missingField("household_name")
        }
        let expiresAt = (json["expires_at"] as? String).flatMap {
            ISO8601DateFormatter.lenientDate(from: $0)
        }
        let fingerprint = (json["certificate_sha256"] as? String)?
            .lowercased()
            .trimmingCharacters(in: .whitespacesAndNewlines)

        return PairingPayload(
            apiBaseURL: url,
            pairingSessionID: sessionID,
            householdID: householdID,
            householdName: householdName,
            expiresAt: expiresAt,
            certificateSHA256: (fingerprint?.isEmpty ?? true) ? nil : fingerprint
        )
    }
}

// ISO8601DateFormatter.lenientDate lives in FamilyCFOShared/Networking/Dates.swift.
