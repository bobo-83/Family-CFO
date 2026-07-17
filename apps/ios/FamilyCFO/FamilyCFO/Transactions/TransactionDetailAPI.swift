import Foundation
import HTTPTypes
import OpenAPIRuntime

/// The transaction-detail surface (M100): the shared screen where any transaction
/// can be categorized, annotated with a free-text note, and have a photo (e.g. a
/// paper check) attached. Uploading a check parses a description off the image and
/// pre-fills the note, which the user can then edit.
protocol TransactionDetailAPI: Sendable {
    func categories() async throws -> [Components.Schemas.Category]
    /// Delete a category (un-categorizes its transactions), so the shared picker's
    /// long-press delete works here too (M96 uniformity).
    func deleteCategory(id: String) async throws
    /// The recent transactions — used by the shared-photo inbox to pick which one
    /// to attach a shared image to.
    func recentTransactions() async throws -> [Components.Schemas.Transaction]
    /// Set/clear the category and/or note. Only the arguments provided are changed.
    func update(
        transactionID: String,
        categoryID: String?,
        clearCategory: Bool,
        note: String?,
        setNote: Bool
    ) async throws -> Components.Schemas.Transaction
    /// Upload a check/receipt image. The returned transaction carries the parsed
    /// description in `note` (unless the user had already written one).
    func uploadAttachment(
        transactionID: String, imageData: Data, contentType: String
    ) async throws -> Components.Schemas.Transaction
    /// The stored image, or nil if there is none.
    func attachmentImage(transactionID: String) async throws -> Data?
    func deleteAttachment(transactionID: String) async throws
}

struct LiveTransactionDetailAPI: TransactionDetailAPI {
    let client: Client

    func categories() async throws -> [Components.Schemas.Category] {
        switch try await client.listCategories(.init()) {
        case .ok(let response): return try response.body.json.categories
        case .unauthorized: throw APIError.unauthorized
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func recentTransactions() async throws -> [Components.Schemas.Transaction] {
        switch try await client.listTransactions(.init(query: .init())) {
        case .ok(let response): return try response.body.json.transactions
        case .unauthorized: throw APIError.unauthorized
        case .unprocessableContent: throw APIError.server(422)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func deleteCategory(id: String) async throws {
        switch try await client.deleteCategory(.init(path: .init(categoryId: id))) {
        case .noContent, .notFound: return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func update(
        transactionID: String,
        categoryID: String?,
        clearCategory: Bool,
        note: String?,
        setNote: Bool
    ) async throws -> Components.Schemas.Transaction {
        // Only send `note` when the caller means to change it — otherwise the
        // server leaves it untouched (it keys off the field being present).
        let request = Components.Schemas.TransactionUpdateRequest(
            categoryId: categoryID,
            clearCategory: clearCategory ? true : nil,
            note: setNote ? note : nil)
        switch try await client.updateTransaction(
            .init(path: .init(transactionId: transactionID), body: .json(request))
        ) {
        case .ok(let response): return try response.body.json
        case .notFound: throw APIError.server(404)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func uploadAttachment(
        transactionID: String, imageData: Data, contentType: String
    ) async throws -> Components.Schemas.Transaction {
        // Send one raw multipart part named "file" carrying the image's real
        // content type — the server rejects anything that isn't an image.
        var fields = HTTPFields()
        fields[.contentType] = contentType
        fields[.init("Content-Disposition")!] =
            #"form-data; name="file"; filename="attachment""#
        let part = OpenAPIRuntime.MultipartRawPart(
            headerFields: fields, body: .init(imageData))
        let body = Operations.UploadTransactionAttachment.Input.Body.multipartForm(
            .init([.undocumented(part)]))
        switch try await client.uploadTransactionAttachment(
            .init(path: .init(transactionId: transactionID), body: body)
        ) {
        case .ok(let response): return try response.body.json
        case .badRequest: throw APIError.server(400)
        case .contentTooLarge: throw APIError.server(413)
        case .notFound: throw APIError.server(404)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func attachmentImage(transactionID: String) async throws -> Data? {
        switch try await client.getTransactionAttachment(
            .init(path: .init(transactionId: transactionID))
        ) {
        case .ok(let response):
            let bytes: OpenAPIRuntime.HTTPBody
            switch response.body {
            case .jpeg(let b): bytes = b
            case .png(let b): bytes = b
            }
            return try await Data(collecting: bytes, upTo: 20 * 1024 * 1024)
        case .notFound: return nil
        case .unauthorized: throw APIError.unauthorized
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func deleteAttachment(transactionID: String) async throws {
        switch try await client.deleteTransactionAttachment(
            .init(path: .init(transactionId: transactionID))
        ) {
        case .noContent, .notFound:
            return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }
}
