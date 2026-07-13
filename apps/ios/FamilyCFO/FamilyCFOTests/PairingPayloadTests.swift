import Foundation
import Testing

@testable import FamilyCFO

struct PairingPayloadTests {
    private func validJSON(overrides: [String: Any?] = [:]) -> String {
        var payload: [String: Any?] = [
            "type": "family-cfo-pairing",
            "version": 1,
            "api_base_url": "https://family-cfo.local:8443/api/v1",
            "pairing_session_id": "secret-session-token",
            "household_id": "11111111-2222-3333-4444-555555555555",
            "household_name": "The Demo Family",
            "expires_at": "2026-07-12T21:30:00.123456+00:00",
            "certificate_sha256": "AB" + String(repeating: "cd", count: 31),
        ]
        for (key, value) in overrides {
            payload[key] = value
        }
        let data = try! JSONSerialization.data(
            withJSONObject: payload.compactMapValues { $0 })
        return String(data: data, encoding: .utf8)!
    }

    @Test func parsesTheDashboardQRPayload() throws {
        let parsed = try PairingPayload.parse(validJSON())

        #expect(parsed.apiBaseURL.absoluteString == "https://family-cfo.local:8443/api/v1")
        #expect(parsed.pairingSessionID == "secret-session-token")
        #expect(parsed.householdName == "The Demo Family")
        // Fingerprint is normalized to lowercase for comparison with SHA-256 hex.
        #expect(parsed.certificateSHA256 == ("ab" + String(repeating: "cd", count: 31)))
        #expect(parsed.expiresAt != nil)
    }

    @Test func fingerprintIsOptional() throws {
        let parsed = try PairingPayload.parse(validJSON(overrides: ["certificate_sha256": nil]))

        #expect(parsed.certificateSHA256 == nil)
    }

    @Test func rejectsForeignQRCodes() {
        #expect(throws: PairingPayload.ParseError.wrongType) {
            try PairingPayload.parse(validJSON(overrides: ["type": "some-other-app"]))
        }
        #expect(throws: PairingPayload.ParseError.notJSON) {
            try PairingPayload.parse("https://example.com/not-json")
        }
    }

    @Test func rejectsUnsupportedVersions() {
        #expect(throws: PairingPayload.ParseError.unsupportedVersion(2)) {
            try PairingPayload.parse(validJSON(overrides: ["version": 2]))
        }
    }

    @Test func rejectsNonHTTPBaseURLs() {
        #expect(throws: PairingPayload.ParseError.invalidBaseURL("ftp://box/api")) {
            try PairingPayload.parse(validJSON(overrides: ["api_base_url": "ftp://box/api"]))
        }
    }

    @Test func parsesTimestampsWithAndWithoutFractionalSeconds() {
        #expect(ISO8601DateFormatter.lenientDate(from: "2026-07-12T21:30:00+00:00") != nil)
        #expect(ISO8601DateFormatter.lenientDate(from: "2026-07-12T21:30:00.123456+00:00") != nil)
        #expect(ISO8601DateFormatter.lenientDate(from: "2026-07-12T21:30:00Z") != nil)
    }
}
