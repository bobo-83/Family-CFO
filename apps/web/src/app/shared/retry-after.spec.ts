import { retryAfterSeconds, tooManyAttemptsMessage } from './retry-after';

function rateLimited(retryAfter?: string): Response {
  return new Response(null, {
    status: 429,
    headers: retryAfter === undefined ? {} : { 'Retry-After': retryAfter },
  });
}

describe('retryAfterSeconds', () => {
  it('accepts only a positive whole number of seconds', () => {
    expect(retryAfterSeconds('900')).toBe(900);
    expect(retryAfterSeconds('  42 ')).toBe(42);
    expect(retryAfterSeconds(null)).toBeNull();
    expect(retryAfterSeconds(undefined)).toBeNull();
    expect(retryAfterSeconds('')).toBeNull();
    expect(retryAfterSeconds('0')).toBeNull();
    expect(retryAfterSeconds('-5')).toBeNull();
    // RFC 9110 also allows an HTTP-date; this API never sends one, and
    // guessing at it would be inventing a figure.
    expect(retryAfterSeconds('Wed, 21 Oct 2015 07:28:00 GMT')).toBeNull();
  });
});

describe('tooManyAttemptsMessage', () => {
  // #92: the load-bearing property. A test pinning one fixed sentence is what
  // let "wait a minute" survive a fifteen-minute lockout.
  it('names the wait the server sent, and changes when the server changes it', () => {
    const quarterHour = tooManyAttemptsMessage(rateLimited('883'));
    const twoMinutes = tooManyAttemptsMessage(rateLimited('120'));

    expect(quarterHour).toBe('Too many attempts — try again in 15 minutes.');
    expect(twoMinutes).toBe('Too many attempts — try again in 2 minutes.');
    expect(quarterHour).not.toBe(twoMinutes);
  });

  it('rounds up, so nobody is sent back before the lockout expires', () => {
    expect(tooManyAttemptsMessage(rateLimited('61'))).toBe(
      'Too many attempts — try again in 2 minutes.',
    );
    expect(tooManyAttemptsMessage(rateLimited('60'))).toBe(
      'Too many attempts — try again in about a minute.',
    );
  });

  it('says under a minute rather than a rounded-down zero', () => {
    expect(tooManyAttemptsMessage(rateLimited('1'))).toBe(
      'Too many attempts — try again in under a minute.',
    );
    expect(tooManyAttemptsMessage(rateLimited('59'))).toBe(
      'Too many attempts — try again in under a minute.',
    );
  });

  it('names no duration when the header is missing or unreadable', () => {
    for (const response of [
      rateLimited(),
      rateLimited('soon'),
      rateLimited('Wed, 21 Oct 2015 07:28:00 GMT'),
      undefined,
    ]) {
      const message = tooManyAttemptsMessage(response);
      expect(message).toBe('Too many attempts — try again later.');
      expect(message).not.toMatch(/\d/);
    }
  });
});
