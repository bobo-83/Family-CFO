import { client } from '../api-client/client.gen';
import { clearAuthState, getToken, setSessionNotice } from './token-store';

let configured = false;

/**
 * The session-ending statuses, split out of `configureApiClient` so specs can
 * exercise the mapping without the generated client.
 *
 * - 401: the token is dead — drop the session; the auth guard lands the user
 *   on the login page on their next navigation.
 * - 423 (ADR 0072 Phase 3): the household is sealed and locked (e.g. after a
 *   box restart). Same re-login flow — a fresh password login unlocks the
 *   household server-side — but the login page shows the server's message so
 *   the user knows why they were signed out.
 */
export async function handleSessionResponse(response: Response): Promise<Response> {
  if (response.status === 401) {
    clearAuthState();
  }
  if (response.status === 423) {
    // Clone: the generated client still needs to read this body.
    const body = (await response
      .clone()
      .json()
      .catch(() => null)) as { error?: { message?: string } } | null;
    setSessionNotice(
      body?.error?.message ??
        "This household's data is sealed and currently locked. Sign in again to unlock it.",
    );
    clearAuthState();
  }
  return response;
}

/**
 * Registers the bearer-token request interceptor and the 401/423 handler on
 * the generated client. Called once from `main.ts` before bootstrap.
 */
export function configureApiClient(): void {
  if (configured) {
    return;
  }
  configured = true;

  client.interceptors.request.use((request) => {
    const token = getToken();
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`);
    }
    // #10 phase 4: the API localizes the prose it authors (error details) from
    // this header. The running bundle's locale IS the household language —
    // that is what compile-time i18n means — so <html lang> is the right
    // source and needs no extra state.
    const locale = document.documentElement.lang;
    if (locale) {
      request.headers.set('Accept-Language', locale);
    }
    return request;
  });

  client.interceptors.response.use(handleSessionResponse);
}
