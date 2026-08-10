import Foundation

/// #92: how long a rate-limited answer actually asks the user to wait.
///
/// The box has always sent the real remaining lockout in `Retry-After`, but the
/// contract did not document the header, so the generated client dropped it and
/// the app printed a constant — "wait a minute" — while the default lockout is
/// fifteen. Someone waited sixty seconds, was refused again, and concluded the
/// app was broken.
///
/// The header stays *advisory*. It reaches the phone through whatever proxy the
/// household put in front of the box, and an older box may not send it at all,
/// so when it is missing or unreadable we say "later" instead of inventing a
/// figure — a number nobody can stand behind is what caused the bug.
enum RetryAfter {
    /// Whole seconds from a `Retry-After` header value; nil when the header is
    /// absent, blank, non-numeric (RFC 9110 also allows an HTTP-date, which this
    /// server never sends), or not a positive wait.
    static func seconds(_ headerValue: String?) -> Int? {
        guard let trimmed = headerValue?.trimmingCharacters(in: .whitespaces),
            let value = Int(trimmed), value > 0
        else { return nil }
        return value
    }

    /// The wait in whole minutes, always rounded **up**: sending someone back
    /// before the lockout expires earns them a second refusal, which is exactly
    /// the failure being fixed.
    static func minutes(_ seconds: Int) -> Int {
        (seconds + 59) / 60
    }

    /// What a screen says when the rate limiter refuses — the real remaining
    /// wait when the server told us, an honest "later" when it did not.
    ///
    /// Deliberately free of plural forms: "under a minute" covers everything
    /// below 60s and "about a minute" covers exactly 60s, so the counted string
    /// is only ever reached with two or more minutes.
    static func tooManyAttemptsMessage(headerValue: String?) -> String {
        guard let seconds = seconds(headerValue) else {
            return String(localized: "Too many attempts — try again later.")
        }
        if seconds < 60 {
            return String(localized: "Too many attempts — try again in under a minute.")
        }
        let waitMinutes = minutes(seconds)
        if waitMinutes == 1 {
            return String(localized: "Too many attempts — try again in about a minute.")
        }
        return String(localized: "Too many attempts — try again in \(waitMinutes) minutes.")
    }
}
