import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

/// Answers every request with the 429 the box sends when the brute-force
/// lockout refuses a login — optionally carrying `Retry-After`, so the tests
/// can also exercise a box or proxy that does not send it.
private struct RateLimitedTransport: ClientTransport {
    let retryAfter: String?

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var fields: HTTPFields = [.contentType: "application/json"]
        if let retryAfter { fields[.retryAfter] = retryAfter }
        let payload = Data(
            #"{"error":{"code":"http_error","message":"Too many login attempts. Try again later."}}"#
                .utf8
        )
        return (HTTPResponse(status: .tooManyRequests, headerFields: fields), HTTPBody(payload))
    }
}

struct RetryAfterTests {
    @Test func parsesOnlyPositiveWholeSeconds() {
        #expect(RetryAfter.seconds("900") == 900)
        #expect(RetryAfter.seconds("  42 ") == 42)
        #expect(RetryAfter.seconds(nil) == nil)
        #expect(RetryAfter.seconds("") == nil)
        #expect(RetryAfter.seconds("0") == nil)
        #expect(RetryAfter.seconds("-5") == nil)
        // RFC 9110 also allows an HTTP-date; this server never sends one, and
        // guessing at it would be inventing a figure.
        #expect(RetryAfter.seconds("Wed, 21 Oct 2015 07:28:00 GMT") == nil)
    }

    @Test func roundsTheWaitUpToWholeMinutes() {
        // 883s of a 900s lockout is 15 minutes, not 14: rounding down would
        // send someone back before the lockout expires.
        #expect(RetryAfter.minutes(883) == 15)
        #expect(RetryAfter.minutes(900) == 15)
        #expect(RetryAfter.minutes(60) == 1)
        #expect(RetryAfter.minutes(61) == 2)
    }

    @Test func messageFollowsTheHeaderRatherThanAConstant() {
        let quarterHour = RetryAfter.tooManyAttemptsMessage(headerValue: "883")
        let twoMinutes = RetryAfter.tooManyAttemptsMessage(headerValue: "119")
        #expect(quarterHour == "Too many attempts — try again in 15 minutes.")
        #expect(twoMinutes == "Too many attempts — try again in 2 minutes.")
        #expect(quarterHour != twoMinutes)
    }

    @Test func subMinuteWaitsSayLessThanAMinuteInsteadOfZero() {
        #expect(
            RetryAfter.tooManyAttemptsMessage(headerValue: "1")
                == "Too many attempts — try again in under a minute.")
        #expect(
            RetryAfter.tooManyAttemptsMessage(headerValue: "59")
                == "Too many attempts — try again in under a minute.")
        #expect(
            RetryAfter.tooManyAttemptsMessage(headerValue: "60")
                == "Too many attempts — try again in about a minute.")
    }

    @Test func namesNoDurationWhenTheServerNamedNone() {
        for header in [nil, "", "soon", "Wed, 21 Oct 2015 07:28:00 GMT"] {
            let message = RetryAfter.tooManyAttemptsMessage(headerValue: header)
            #expect(message == "Too many attempts — try again later.")
            // The whole bug was a duration the client could not stand behind.
            #expect(message.rangeOfCharacter(from: .decimalDigits) == nil)
        }
    }
}

@MainActor
struct LoginRateLimitMessageTests {
    private func signInMessage(retryAfter: String?) async -> String? {
        let baseURL = URL(string: "https://box.invalid/api/v1")!
        let viewModel = LoginViewModel(
            step: .credentials(baseURL: baseURL, fingerprint: nil),
            buildClient: { url, _ in
                Client(serverURL: url, transport: RateLimitedTransport(retryAfter: retryAfter))
            }
        )
        viewModel.email = "someone@example.invalid"
        viewModel.password = "wrong-password"
        await viewModel.signIn(into: AppModel())
        return viewModel.signInError
    }

    /// The load-bearing one: the sentence has to come from the header the
    /// server sent, not from a constant that happened to read plausibly.
    @Test func signInMessageReflectsTheServersRetryAfter() async {
        #expect(await signInMessage(retryAfter: "900") == "Too many attempts — try again in 15 minutes.")
        #expect(await signInMessage(retryAfter: "120") == "Too many attempts — try again in 2 minutes.")
        #expect(await signInMessage(retryAfter: "30") == "Too many attempts — try again in under a minute.")
    }

    /// An older box, or a proxy that strips the header: say "later" rather
    /// than a duration the app cannot know.
    @Test func signInMessageDegradesWhenTheHeaderIsAbsent() async {
        #expect(await signInMessage(retryAfter: nil) == "Too many attempts — try again later.")
        #expect(await signInMessage(retryAfter: "not-a-number") == "Too many attempts — try again later.")
    }

    /// A refused sign-in returns to the credentials step so the person can
    /// retry once the wait is over — it is not a dead end.
    @Test func rateLimitedSignInStaysOnTheCredentialsStep() async {
        let baseURL = URL(string: "https://box.invalid/api/v1")!
        let viewModel = LoginViewModel(
            step: .credentials(baseURL: baseURL, fingerprint: nil),
            buildClient: { url, _ in
                Client(serverURL: url, transport: RateLimitedTransport(retryAfter: "900"))
            }
        )
        await viewModel.signIn(into: AppModel())
        #expect(viewModel.step == .credentials(baseURL: baseURL, fingerprint: nil))
    }
}
