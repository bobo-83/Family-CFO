import { handleSessionResponse } from './api-client-setup';
import { authState, clearAuthState, setAuthState } from './token-store';

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
  });

  it('leaves an ok response and the session alone', async () => {
    signIn();

    const response = new Response('{}', { status: 200 });
    expect(await handleSessionResponse(response)).toBe(response);

    expect(authState()).not.toBeNull();
  });

  it('drops the session on a 401', async () => {
    signIn();

    await handleSessionResponse(new Response('{}', { status: 401 }));

    expect(authState()).toBeNull();
  });

  // #101: a 423 is treated EXACTLY like a 401 — session dropped, nothing said.
  // On the web a 423 always means "sign in and this resolves itself", because
  // the password login IS the unlock. Announcing the lock on the login screen
  // read like rejected credentials.
  it('drops the session on a 423 without telling the user anything', async () => {
    signIn();

    const body = JSON.stringify({
      error: { code: 'household_locked', message: 'locked' },
    });
    const response = new Response(body, { status: 423 });
    await handleSessionResponse(response);

    expect(authState()).toBeNull();
    // The caller's body is untouched.
    expect(await response.text()).toBe(body);
  });

  it('drops the session on a 423 with no body at all', async () => {
    signIn();

    await handleSessionResponse(new Response(null, { status: 423 }));

    expect(authState()).toBeNull();
  });
});
