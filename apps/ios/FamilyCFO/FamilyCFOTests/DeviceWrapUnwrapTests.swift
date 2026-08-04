import CryptoKit
import Foundation
import Testing

@testable import FamilyCFO

/// ADR 0072 Phase 3: the local half of the device auto-unlock. The fixture
/// wrap is generated in-test with the same ECIES construction the server
/// uses (P256 ECDH → HKDF-SHA256, empty salt, info "family-cfo-device-wrap"
/// → AES-GCM), so the decode path is validated without server fixtures.
struct DeviceWrapUnwrapTests {
    /// A urlsafe-base64-shaped DEK, like the server's Fernet key.
    private let dek = "dGVzdC1kYXRhLWtleS1ub3QtYS1yZWFsLW9uZT0="

    private func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    private func makeWrap(
        of plaintext: String, for devicePublicKey: P256.KeyAgreement.PublicKey
    ) throws -> String {
        let ephemeral = P256.KeyAgreement.PrivateKey()
        let sharedSecret = try ephemeral.sharedSecretFromKeyAgreement(with: devicePublicKey)
        let key = sharedSecret.hkdfDerivedSymmetricKey(
            using: SHA256.self, salt: Data(),
            sharedInfo: Data("family-cfo-device-wrap".utf8), outputByteCount: 32)
        let sealed = try AES.GCM.seal(Data(plaintext.utf8), using: key)
        let nonce = sealed.nonce.withUnsafeBytes { Data($0) }
        return """
            {"v":1,"alg":"ecies-p256-hkdf-aesgcm","epk":"\(hex(ephemeral.publicKey.x963Representation))","nonce":"\(hex(nonce))","ct":"\(hex(sealed.ciphertext + sealed.tag))"}
            """
    }

    @Test func unwrapsAWrapMadeForThisDevice() throws {
        // The pairing key is minted as a Signing key; the unlock path imports
        // the same raw bytes for key agreement — mirror that here.
        let signingKey = P256.Signing.PrivateKey()
        let agreementKey = try P256.KeyAgreement.PrivateKey(
            rawRepresentation: signingKey.rawRepresentation)
        let wrapJSON = try makeWrap(of: dek, for: agreementKey.publicKey)

        let unwrapped = try DeviceWrapUnwrapper.unwrap(
            wrapJSON: wrapJSON, devicePrivateKeyRaw: signingKey.rawRepresentation)

        #expect(unwrapped == dek)
    }

    @Test func rejectsAWrapMadeForAnotherDevice() throws {
        let rightKey = P256.KeyAgreement.PrivateKey()
        let wrongKey = P256.KeyAgreement.PrivateKey()
        let wrapJSON = try makeWrap(of: dek, for: rightKey.publicKey)

        #expect(throws: (any Error).self) {
            try DeviceWrapUnwrapper.unwrap(
                wrapJSON: wrapJSON, devicePrivateKeyRaw: wrongKey.rawRepresentation)
        }
    }

    @Test func rejectsAnUnknownAlgorithmOrMangledFields() throws {
        let key = P256.KeyAgreement.PrivateKey()
        let wrapJSON = try makeWrap(of: dek, for: key.publicKey)

        let downgraded = wrapJSON.replacingOccurrences(
            of: "ecies-p256-hkdf-aesgcm", with: "none")
        #expect(throws: HouseholdUnlockError.malformedWrap) {
            try DeviceWrapUnwrapper.unwrap(
                wrapJSON: downgraded, devicePrivateKeyRaw: key.rawRepresentation)
        }
        #expect(throws: HouseholdUnlockError.malformedWrap) {
            try DeviceWrapUnwrapper.unwrap(
                wrapJSON: "not json", devicePrivateKeyRaw: key.rawRepresentation)
        }
    }

    @Test func hexDecodingIsStrict() {
        #expect(Data(hexEncoded: "0aff") == Data([0x0a, 0xff]))
        #expect(Data(hexEncoded: "0AFF") == Data([0x0a, 0xff]))
        #expect(Data(hexEncoded: "abc") == nil)
        #expect(Data(hexEncoded: "zz") == nil)
        #expect(Data(hexEncoded: "") == Data())
    }
}
