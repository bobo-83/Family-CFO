import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

/// Captures the JSON body the generated client actually puts on the wire, so
/// these tests assert the request as the server will see it — not as the Swift
/// types suggest it should be.
final class CapturingTransport: ClientTransport, @unchecked Sendable {
    private(set) var body: [String: Any]?
    private let response: Components.Schemas.ChatResponse

    init(response: Components.Schemas.ChatResponse) {
        self.response = response
    }

    func send(
        _ request: HTTPRequest,
        body requestBody: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        if let requestBody {
            let data = try await Data(collecting: requestBody, upTo: 20 * 1024 * 1024)
            body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        }
        let payload = try JSONEncoder().encode(response)
        return (
            HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]),
            HTTPBody(payload)
        )
    }
}

struct AdvisorAPIRequestTests {
    private func stubbedAPI() -> (LiveAdvisorAPI, CapturingTransport) {
        let response = Components.Schemas.ChatResponse(
            conversationId: "conv-1",
            recommendation: .init(
                id: "rec-1",
                answer: "Your grocery spend is $412.",
                assumptions: [],
                impacts: [],
                tradeoffs: [],
                alternatives: [],
                confidence: 0.9,
                calculationRefs: [],
                warnings: []
            )
        )
        let transport = CapturingTransport(response: response)
        let client = Client(
            serverURL: URL(string: "https://box.local")!,
            transport: transport
        )
        return (LiveAdvisorAPI(client: client), transport)
    }

    /// The whole point of M85's model split: a CSV must ride `data_file_*`, and
    /// must NOT be smuggled into the image fields — the server would hand it to
    /// the vision describer instead of the data-file preview.
    @Test func dataFileRidesTheDataFileFieldsNotTheImageFields() async throws {
        let (api, transport) = stubbedAPI()
        let csv = ChatAttachment(
            data: Data("month,spend\nJan,412\n".utf8),
            kind: .dataFile,
            displayName: "groceries.csv"
        )

        _ = try await api.sendMessage("What did we spend?", conversationID: nil, attachment: csv)

        let body = try #require(transport.body)
        #expect(body["data_file_name"] as? String == "groceries.csv")
        #expect(body["data_file_base64"] as? String == Data("month,spend\nJan,412\n".utf8).base64EncodedString())
        #expect(body["image_base64"] == nil)
        #expect(body["image_media_type"] == nil)
    }

    /// The converse: M84's vision path must be untouched by the M85 split.
    @Test func visualAttachmentStillRidesTheImageFields() async throws {
        let (api, transport) = stubbedAPI()
        let pdf = ChatAttachment(
            data: Data("%PDF-1.7".utf8),
            kind: .visual(.applicationPdf),
            displayName: "statement.pdf"
        )

        _ = try await api.sendMessage("Read this", conversationID: nil, attachment: pdf)

        let body = try #require(transport.body)
        #expect(body["image_media_type"] as? String == "application/pdf")
        #expect(body["image_base64"] as? String == Data("%PDF-1.7".utf8).base64EncodedString())
        #expect(body["data_file_base64"] == nil)
        #expect(body["data_file_name"] == nil)
    }

    @Test func messageWithoutAttachmentCarriesNeitherPayload() async throws {
        let (api, transport) = stubbedAPI()

        _ = try await api.sendMessage("Can we afford a car?", conversationID: "c-9", attachment: nil)

        let body = try #require(transport.body)
        #expect(body["message"] as? String == "Can we afford a car?")
        #expect(body["conversation_id"] as? String == "c-9")
        #expect(body["image_base64"] == nil)
        #expect(body["data_file_base64"] == nil)
    }
}
