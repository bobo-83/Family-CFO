import CryptoKit
import Foundation
import Observation
import UIKit

@MainActor
@Observable
final class PairingViewModel {
    enum Step: Equatable {
        case scanning
        case confirming(PairingPayload)
        case pairing
        case failed(String)
    }

    private(set) var step: Step = .scanning
    var deviceName: String = UIDevice.current.name

    /// Feed a scanned (or pasted) QR string; moves to the confirm step so
    /// the user verifies the server identity before anything is sent.
    func handleScanned(_ raw: String) {
        do {
            let payload = try PairingPayload.parse(raw)
            if let expiresAt = payload.expiresAt, expiresAt < .now {
                step = .failed("This pairing code has expired. Generate a fresh one on the dashboard's Devices page.")
                return
            }
            step = .confirming(payload)
        } catch PairingPayload.ParseError.wrongType, PairingPayload.ParseError.notJSON {
            step = .failed("That doesn't look like a Family CFO pairing code.")
        } catch PairingPayload.ParseError.unsupportedVersion(let version) {
            step = .failed("This pairing code is version \(version); the app understands version \(PairingPayload.supportedVersion). Update the app.")
        } catch {
            step = .failed("The pairing code is incomplete: \(error)")
        }
    }

    func cancelConfirmation() {
        step = .scanning
    }

    func pair(payload: PairingPayload, into model: AppModel) async {
        step = .pairing
        // The private key stays on the device; the server stores the public
        // half alongside the paired device (future request-signing seam).
        let privateKey = P256.Signing.PrivateKey()
        let client = APIClientFactory.makeClient(
            baseURL: payload.apiBaseURL,
            pinnedCertificateSHA256: payload.certificateSHA256
        )
        do {
            let output = try await client.confirmPairing(
                .init(
                    body: .json(
                        .init(
                            pairingSessionId: payload.pairingSessionID,
                            deviceName: deviceName.isEmpty ? "iPhone" : deviceName,
                            devicePublicKey: privateKey.publicKey.rawRepresentation
                                .base64EncodedString()
                        )
                    )
                )
            )
            switch output {
            case .ok(let response):
                let credential = try response.body.json
                try? KeychainStore.save(
                    privateKey.rawRepresentation, account: "device-private-key")
                model.completePairing(
                    server: ServerConfig(
                        apiBaseURL: payload.apiBaseURL,
                        certificateSHA256: payload.certificateSHA256,
                        householdID: payload.householdID,
                        householdName: payload.householdName,
                        deviceName: deviceName
                    ),
                    credential: StoredCredential(
                        deviceID: credential.deviceId,
                        accessToken: credential.accessToken,
                        expiresAt: credential.expiresAt,
                        role: credential.role,
                        roleName: credential.roleName,
                        rights: credential.rights
                    )
                )
            case .badRequest:
                step = .failed("The pairing code was already used or has expired. Generate a fresh one on the dashboard.")
            case .undocumented(let status, _):
                step = .failed("The server answered with an unexpected status (\(status)).")
            }
        } catch {
            step = .failed(Self.describeTransportFailure(error, pinned: payload.certificateSHA256 != nil))
        }
    }

    static func describeTransportFailure(_ error: Error, pinned: Bool) -> String {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled && pinned {
            return "The server's certificate does not match the fingerprint in the pairing code. If the certificate was rotated, generate a new pairing QR."
        }
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorServerCertificateUntrusted {
            return "The server uses a self-signed certificate but the pairing code carried no fingerprint to pin. Check the server's FAMILY_CFO_TLS_CERT_PATH."
        }
        return "Could not reach the server: make sure this phone is on the same network (or tailnet) as your Family CFO box."
    }
}
