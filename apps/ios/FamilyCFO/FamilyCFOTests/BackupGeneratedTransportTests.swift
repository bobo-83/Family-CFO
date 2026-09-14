import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

private actor BackupGeneratedTransport: ClientTransport {
    private let responseJSON = Data(
        #"{"frequency":"daily","smb_host":"nas.local","smb_share":"backups","smb_folder":"family-cfo","smb_username":"backup-user","smb_domain":"HOME","has_password":true,"local_retention":{"mode":"tiered","keep_all_days":3,"daily_until_days":14,"weekly_until_days":90},"offbox_retention":{"mode":"keep_all"},"local_min_free_bytes":1000000000,"offbox_min_free_bytes":2000000000,"legacy_conflict_detected":false,"retention_review_required":false,"retention_activated_at":"2026-09-12T12:34:56.123456Z","revision":"opaque-revision:1234567890","updated_at":"2026-09-12T12:34:56.654321Z"}"#.utf8)
    private var putJSON: [String: Any]?

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        if operationID == Operations.UpdateBackupConfig.id, let body {
            let data = try await Data(collecting: body, upTo: 1_000_000)
            putJSON = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        }
        return (
            HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]),
            HTTPBody(responseJSON))
    }

    func capturedPutJSON() -> [String: Any]? { putJSON }
}

private struct BackupPreconditionTransport: ClientTransport {
    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let payload = Data(
            #"{"error":{"code":"precondition_required","message":"Reload before saving."}}"#.utf8)
        return (
            HTTPResponse(
                status: .init(code: 428),
                headerFields: [.contentType: "application/json"]),
            HTTPBody(payload))
    }
}

struct BackupGeneratedTransportTests {
    @Test func generatedPreconditionRequiredResponseMapsToTypedBackupError() async {
        let client = Client(
            serverURL: URL(string: "https://box.local")!,
            configuration: Configuration(dateTranscoder: LenientDateTranscoder()),
            transport: BackupPreconditionTransport())
        let api = LiveBackupAPI(client: client)
        let draft = BackupConfigDraft(
            frequency: .daily, host: "", share: "", folder: "", username: "",
            password: nil, domain: "", localRetention: .init(),
            offboxRetention: .init(), expectedRevision: "opaque-revision",
            confirmRetentionPolicy: true)

        do {
            _ = try await api.updateConfig(draft)
            Issue.record("expected HTTP 428 to throw")
        } catch let error as BackupError {
            guard case .configurationPreconditionRequired(let message) = error else {
                Issue.record("expected configurationPreconditionRequired")
                return
            }
            #expect(message == "Reload before saving.")
        } catch {
            Issue.record("expected typed BackupError")
        }
    }

    @Test func generatedTransportEchoesOpaqueRevisionAndOmitsTimestampCAS() async throws {
        let transport = BackupGeneratedTransport()
        let client = Client(
            serverURL: URL(string: "https://box.local")!,
            configuration: Configuration(dateTranscoder: LenientDateTranscoder()),
            transport: transport)
        let api = LiveBackupAPI(client: client)

        let config = try await api.config()
        #expect(config.revision == "opaque-revision:1234567890")

        let draft = BackupConfigDraft(
            frequency: .daily,
            host: config.smbHost ?? "",
            share: config.smbShare ?? "",
            folder: config.smbFolder ?? "",
            username: config.smbUsername ?? "",
            password: nil,
            domain: config.smbDomain ?? "",
            localRetention: .init(),
            offboxRetention: .init(mode: .keepAll),
            expectedRevision: config.revision,
            confirmRetentionPolicy: false)
        _ = try await api.updateConfig(draft)

        let payload = await transport.capturedPutJSON()
        #expect(payload?["expected_revision"] as? String == "opaque-revision:1234567890")
        #expect(payload?["expected_updated_at"] == nil)
    }
}
