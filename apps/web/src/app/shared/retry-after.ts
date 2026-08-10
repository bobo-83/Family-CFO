/**
 * #92: how long a rate-limited answer actually asks the user to wait.
 *
 * The API has always sent the real remaining lockout in `Retry-After`, but the
 * contract did not document the header, so no client read it and the apps said
 * "wait a minute" while the default lockout is fifteen. Someone waited sixty
 * seconds, was refused again, and read the wait as a fault.
 *
 * The header stays advisory: it reaches the browser through whatever proxy the
 * household put in front of the box, and an older box may not send it at all.
 * When it is missing or unreadable we say "later" rather than invent a figure.
 */

/** Whole seconds from a `Retry-After` value; null when it is absent, blank,
 * non-numeric (RFC 9110 also allows an HTTP-date, which this API never sends),
 * or not a positive wait. */
export function retryAfterSeconds(headerValue: string | null | undefined): number | null {
  const trimmed = headerValue?.trim();
  if (!trimmed || !/^\d+$/.test(trimmed)) {
    return null;
  }
  const seconds = Number(trimmed);
  return seconds > 0 ? seconds : null;
}

/**
 * What a screen says when the rate limiter refuses.
 *
 * The wait is rounded **up** to whole minutes — sending someone back before the
 * lockout expires earns them a second refusal, which is the failure being
 * fixed. No plural forms: "under a minute" covers everything below 60s and
 * "about a minute" covers exactly 60s, so the counted string is only ever
 * reached with two or more minutes.
 */
export function tooManyAttemptsMessage(response: Response | undefined): string {
  const seconds = retryAfterSeconds(response?.headers?.get('Retry-After'));
  if (seconds === null) {
    return $localize`Too many attempts — try again later.`;
  }
  if (seconds < 60) {
    return $localize`Too many attempts — try again in under a minute.`;
  }
  const minutes = Math.ceil(seconds / 60);
  if (minutes === 1) {
    return $localize`Too many attempts — try again in about a minute.`;
  }
  return $localize`Too many attempts — try again in ${minutes} minutes.`;
}
