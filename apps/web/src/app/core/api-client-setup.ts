import { client } from '../api-client/client.gen';
import { clearAuthState, getToken } from './token-store';

let configured = false;

/**
 * The session-ending statuses, split out of `configureApiClient` so specs can
 * exercise the mapping without the generated client.
 *
 * - 401: the token is dead — drop the session; the auth guard lands the user
 *   on the login page on their next navigation.
 * - 423 (ADR 0072 Phase 3): the household is sealed and locked (e.g. after a
 *   box restart). Treated EXACTLY like 401 — no notice (#101). On the web a 423
 *   always means "sign in and this resolves itself": the password login IS the
 *   unlock (auth.py -> on_password_established). Telling someone their
 *   household is locked, on the one screen whose purpose is to unlock it, reads
 *   like rejected credentials and adds nothing the next click was not already
 *   about to do.
 *
 *   The case that DOES deserve words is the inverse — signing in successfully
 *   and STILL being locked, which happens to a member with no usable key wrap.
 *   That member is genuinely stuck, and saying so belongs after a successful
 *   login, not before one (#101).
 */
export async function handleSessionResponse(response: Response): Promise<Response> {
  if (response.status === 401) {
    clearAuthState();
  }
  if (response.status === 423) {
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
