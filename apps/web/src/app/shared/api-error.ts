import { tooManyAttemptsMessage } from './retry-after';

interface StructuredApiError {
  error?: { message?: string };
}

/**
 * The message to show for a failed API call.
 *
 * `response` is optional and only changes the answer for a 429: the rate
 * limiter's own prose says "try again later" with no figure, while the
 * `Retry-After` header carries the real remaining lockout (#92). Pass it on
 * every path a person can be rate-limited on. Endpoints whose 429 body already
 * names the wait — the backup cooldown — read better without it.
 */
export function apiErrorMessage(error: unknown, fallback: string, response?: Response): string {
  if (response?.status === 429) {
    return tooManyAttemptsMessage(response);
  }
  const structured = error as StructuredApiError | undefined;
  return structured?.error?.message ?? fallback;
}
