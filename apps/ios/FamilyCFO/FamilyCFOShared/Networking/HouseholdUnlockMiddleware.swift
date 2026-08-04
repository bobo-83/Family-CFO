import CryptoKit
import Foundation
import HTTPTypes
import OpenAPIRuntime

/// ADR 0072 Phase 3: a sealed household with no live key session (e.g. after a
/// box restart) answers 423 `household_locked` on ANY endpoint. This paired
/// device holds a wrap of the household data key, so it can open a key session
/// itself: fetch its wrap, unwrap it locally with the pairing private key, post
/// the key back, and replay the original request — ONE attempt, no loops. Any
/// failure returns the original 423 to the normal error path.
struct HouseholdUnlockMiddleware: ClientMiddleware {
    /// The device's pairing private key (P256 rawRepresentation) from the
    /// Keychain — a closure so this shared type never touches the Keychain.
    let devicePrivateKey: @Sendable () -> Data?

    /// JSON bodies only in this app; anything larger than this is not a
    /// request worth replaying.
    private static let bufferLimit = 64 * 1024 * 1024

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        // Buffer the request body up front: HTTPBody streams are single-shot,
        // and the request may need one replay after an unlock.
        let bufferedBody: Data?
        if let body {
            bufferedBody = try await Data(collecting: body, upTo: Self.bufferLimit)
        } else {
            bufferedBody = nil
        }
        let (response, responseBody) = try await next(
            request, bufferedBody.map { HTTPBody($0) }, baseURL)
        guard response.status.code == 423,
            operationID != Operations.GetDeviceWrap.id,
            operationID != Operations.OpenKeySession.id
        else {
            return (response, responseBody)
        }
        do {
            try await openKeySession(via: next, baseURL: baseURL)
        } catch {
            // Surface the original 423 through the normal error display.
            return (response, responseBody)
        }
        return try await next(request, bufferedBody.map { HTTPBody($0) }, baseURL)
    }

    /// GET the device's wrap, unwrap it locally, POST the key back. The
    /// sub-requests run through `next`, so the bearer middleware downstream
    /// authenticates them like any other call.
    private func openKeySession(
        via next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?),
        baseURL: URL
    ) async throws {
        guard let privateKeyData = devicePrivateKey() else {
            throw HouseholdUnlockError.noDeviceKey
        }
        var wrapRequest = HTTPRequest(
            method: .get, scheme: nil, authority: nil, path: "/household/device-wrap")
        wrapRequest.headerFields[.accept] = "application/json"
        let (wrapResponse, wrapBody) = try await next(wrapRequest, nil, baseURL)
        guard wrapResponse.status.code == 200, let wrapBody else {
            throw HouseholdUnlockError.noWrapForThisDevice
        }
        let wrapData = try await Data(collecting: wrapBody, upTo: 1024 * 1024)
        struct WrapEnvelope: Decodable {
            let wrapJson: String
            enum CodingKeys: String, CodingKey { case wrapJson = "wrap_json" }
        }
        let envelope = try JSONDecoder().decode(WrapEnvelope.self, from: wrapData)
        let dek = try DeviceWrapUnwrapper.unwrap(
            wrapJSON: envelope.wrapJson, devicePrivateKeyRaw: privateKeyData)

        var sessionRequest = HTTPRequest(
            method: .post, scheme: nil, authority: nil, path: "/household/key-session")
        sessionRequest.headerFields[.accept] = "application/json"
        sessionRequest.headerFields[.contentType] = "application/json"
        let payload = try JSONEncoder().encode(["dek": dek])
        let (sessionResponse, _) = try await next(sessionRequest, HTTPBody(payload), baseURL)
        guard sessionResponse.status.code == 200 else {
            throw HouseholdUnlockError.keyRejected
        }
    }
}

enum HouseholdUnlockError: Error {
    case noDeviceKey
    case noWrapForThisDevice
    case malformedWrap
    case keyRejected
}

/// The local half of the device unlock: open the server's ECIES wrap
/// (`{v:1, alg:"ecies-p256-hkdf-aesgcm", epk, nonce, ct}`, hex fields) with
/// the pairing private key. Pure math — unit-tested against a wrap produced
/// the same way.
enum DeviceWrapUnwrapper {
    static let sharedInfo = Data("family-cfo-device-wrap".utf8)

    static func unwrap(wrapJSON: String, devicePrivateKeyRaw: Data) throws -> String {
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(wrapJSON.utf8))
                as? [String: Any],
            object["v"] as? Int == 1,
            object["alg"] as? String == "ecies-p256-hkdf-aesgcm",
            let epk = (object["epk"] as? String).flatMap(Data.init(hexEncoded:)),
            let nonce = (object["nonce"] as? String).flatMap(Data.init(hexEncoded:)),
            let ciphertextAndTag = (object["ct"] as? String).flatMap(Data.init(hexEncoded:)),
            ciphertextAndTag.count > 16
        else {
            throw HouseholdUnlockError.malformedWrap
        }
        // The pairing key is stored as P256.Signing rawRepresentation — the
        // same raw scalar imports as a KeyAgreement key.
        let privateKey = try P256.KeyAgreement.PrivateKey(rawRepresentation: devicePrivateKeyRaw)
        let ephemeralKey = try P256.KeyAgreement.PublicKey(x963Representation: epk)
        let sharedSecret = try privateKey.sharedSecretFromKeyAgreement(with: ephemeralKey)
        let key = sharedSecret.hkdfDerivedSymmetricKey(
            using: SHA256.self, salt: Data(), sharedInfo: sharedInfo, outputByteCount: 32)
        let box = try AES.GCM.SealedBox(
            nonce: .init(data: nonce),
            ciphertext: ciphertextAndTag.dropLast(16),
            tag: ciphertextAndTag.suffix(16))
        let dek = try AES.GCM.open(box, using: key)
        return String(decoding: dek, as: UTF8.self)
    }
}

extension Data {
    /// Strict lowercase/uppercase hex; nil on odd length or non-hex bytes.
    init?(hexEncoded string: String) {
        guard string.count.isMultiple(of: 2) else { return nil }
        var data = Data(capacity: string.count / 2)
        var index = string.startIndex
        while index < string.endIndex {
            let next = string.index(index, offsetBy: 2)
            guard let byte = UInt8(string[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        self = data
    }
}
