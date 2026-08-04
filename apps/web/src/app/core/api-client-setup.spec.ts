import { handleSessionResponse } from './api-client-setup';
import { authState, clearAuthState, consumeSessionNotice, setAuthState } from './token-store';

function signIn() {
  setAuthState({
    accessToken: 'token-123',
    householdId: 'household-1',
    userId: 'user-1',
    role: 'owner',
  });
}

describe('handleSessionResponse', () => {
  beforeEach(() => {
    localStorage.clear();
    clearAuthState();
    consumeSessionNotice();
  });

  it('leaves an ok response and the session alone', async () => {
    signIn();

    const response = new Response('{}', { status: 200 });
    expect(await handleSessionResponse(response)).toBe(response);

    expect(authState()).not.toBeNull();
    expect(consumeSessionNotice()).toBeNull();
  });

  it('drops the session on a 401', async () => {
    signIn();

    await handleSessionResponse(new Response('{}', { status: 401 }));

    expect(authState()).toBeNull();
    expect(consumeSessionNotice()).toBeNull();
  });

  // ADR 0072 Phase 3: a sealed household with no live key session answers 423
  // anywhere — drop the session and carry the server's message to the login
  // page (a fresh password login unlocks the household server-side).
  it('maps a 423 to re-login with the server message', async () => {
    signIn();

    const body = JSON.stringify({
      error: {
        code: 'household_locked',
        message:
          "This household's data is sealed and currently locked. Sign in again to unlock it.",
      },
    });
    const response = new Response(body, { status: 423 });
    await handleSessionResponse(response);

    expect(authState()).toBeNull();
    expect(consumeSessionNotice()).toBe(
      "This household's data is sealed and currently locked. Sign in again to unlock it.",
    );
    // The caller's body is untouched (the handler reads a clone).
    expect(await response.text()).toBe(body);
  });

  it('falls back to a canned message on a 423 without a body', async () => {
    signIn();

    await handleSessionResponse(new Response(null, { status: 423 }));

    expect(authState()).toBeNull();
    expect(consumeSessionNotice()).toBe(
      "This household's data is sealed and currently locked. Sign in again to unlock it.",
    );
  });
});
