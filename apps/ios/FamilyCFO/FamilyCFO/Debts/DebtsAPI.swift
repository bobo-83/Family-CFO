import Foundation

/// Loans — mortgage, auto, student, and other installment debt (M96). Unlike a
/// credit card (paid in full) or a bill (no balance), a loan has a balance owed
/// AND a fixed monthly payment: the payment is committed in safe-to-spend, and the
/// balance is tracked in net worth and total debt. Modelled as a liability account
/// whose `minimum_payment` is the fixed monthly payment.
protocol DebtsAPI: Sendable {
    func loans() async throws -> [Components.Schemas.Account]
    func addLoan(_ draft: LoanDraft) async throws
    func updateLoan(id: String, _ draft: LoanDraft) async throws
    func deleteLoan(id: String) async throws
    /// Read a photographed loan/lease statement into candidate values (the user
    /// confirms and edits before saving). Nothing is stored by the scan.
    func scanStatement(_ attachment: ChatAttachment) async throws -> Components.Schemas.LoanScanResult
}

/// The liability account types offered as "loans" (credit cards are handled
/// separately via the pay-in-full setting).
let loanAccountTypes: [Components.Schemas.AccountType] = [
    .mortgage, .autoLoan, .studentLoan, ._401kLoan, .otherLiability,
]

struct LoanDraft {
    var name: String
    var type: Components.Schemas.AccountType
    var currency: String
    var balanceOwedMinor: Int64  // what you still owe, as a positive amount
    var monthlyPaymentMinor: Int64
    var aprPercent: Double?
    var maturityDate: String?  // ISO "yyyy-MM-dd", the loan/lease end date
    var nextPaymentDueDate: String?  // ISO "yyyy-MM-dd", next payment due (ADR 0033)
}

struct LiveDebtsAPI: DebtsAPI {
    let client: Client

    func loans() async throws -> [Components.Schemas.Account] {
        switch try await client.listAccounts(.init()) {
        case .ok(let response):
            return try response.body.json.accounts.filter { loanAccountTypes.contains($0._type) }
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func addLoan(_ draft: LoanDraft) async throws {
        let request = Components.Schemas.AccountCreateRequest(
            name: draft.name,
            _type: draft.type,
            currency: draft.currency,
            // Always send a rate (0 when unknown): a loan needs both terms present
            // for its monthly payment to be counted as committed in safe-to-spend.
            // The draft holds a percent; the contract stores a fraction (ADR 0042).
            annualInterestRate: (draft.aprPercent ?? 0) / 100,
            minimumPayment: .init(amountMinor: draft.monthlyPaymentMinor, currency: draft.currency),
            maturityDate: draft.maturityDate,
            nextPaymentDueDate: draft.nextPaymentDueDate
        )
        let created: Components.Schemas.Account
        switch try await client.createAccount(.init(body: .json(request))) {
        case .created(let response):
            created = try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }

        // A liability carries a NEGATIVE balance — the amount owed.
        let balance = Components.Schemas.AccountBalanceCreateRequest(
            balance: .init(amountMinor: -draft.balanceOwedMinor, currency: draft.currency)
        )
        switch try await client.recordAccountBalance(
            .init(path: .init(accountId: created.id), body: .json(balance))
        ) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateLoan(id: String, _ draft: LoanDraft) async throws {
        let request = Components.Schemas.AccountUpdateRequest(
            name: draft.name,
            _type: draft.type,
            // The draft holds a percent; the contract stores a fraction (ADR 0042).
            annualInterestRate: (draft.aprPercent ?? 0) / 100,
            minimumPayment: .init(amountMinor: draft.monthlyPaymentMinor, currency: draft.currency),
            maturityDate: draft.maturityDate,
            nextPaymentDueDate: draft.nextPaymentDueDate
        )
        switch try await client.updateAccount(.init(path: .init(accountId: id), body: .json(request))) {
        case .ok:
            break
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }

        let balance = Components.Schemas.AccountBalanceCreateRequest(
            balance: .init(amountMinor: -draft.balanceOwedMinor, currency: draft.currency)
        )
        switch try await client.recordAccountBalance(
            .init(path: .init(accountId: id), body: .json(balance))
        ) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.LoanScanResult {
        guard case .visual(let mediaType) = attachment.kind,
            let scanMediaType = Components.Schemas.LoanScanRequest.ImageMediaTypePayload(
                rawValue: mediaType.rawValue)
        else {
            throw APIError.server(415)
        }
        let request = Components.Schemas.LoanScanRequest(
            imageBase64: attachment.data.base64EncodedString(),
            imageMediaType: scanMediaType
        )
        switch try await client.scanLoanStatement(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .unprocessableContent:
            throw APIError.server(422)
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteLoan(id: String) async throws {
        switch try await client.deleteAccount(.init(path: .init(accountId: id))) {
        case .noContent:
            return
        case .notFound:
            return  // already gone
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict:
            throw APIError.server(409)  // has dependent data (e.g. transactions)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
