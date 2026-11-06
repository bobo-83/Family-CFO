import { vi } from 'vitest';
import { fetchProtected, handleSessionResponse } from './api-client-setup';
import { authGeneration, authState, clearAuthState, setAuthState } from './token-store';

function signIn() {
  setAuthState({
    accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
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
    expect(await handleSessionResponse(response, authGeneration())).toBe(response);

    expect(authState()).not.toBeNull();
  });

  it('drops the session on a 401', async () => {
    signIn();

    await handleSessionResponse(new Response('{}', { status: 401 }), authGeneration());

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
    await handleSessionResponse(response, authGeneration());

    expect(authState()).toBeNull();
    // The caller's body is untouched.
    expect(await response.text()).toBe(body);
  });

  it('drops the session on a 423 with no body at all', async () => {
    signIn();

    await handleSessionResponse(new Response(null, { status: 423 }), authGeneration());

    expect(authState()).toBeNull();
  });

  // #103: the one 423 that answers an ACTION rather than a stale session —
  // adding a member, or accepting an invite, into a sealed household whose key
  // is not readable. Ending the owner's session here would boot them off the
  // very form whose error message just told them to sign in.
  it('keeps the session on a 423 that refuses a new member', async () => {
    signIn();

    const body = JSON.stringify({
      error: {
        code: 'household_locked_new_member',
        message: 'Sign in to this household first, then add the member.',
      },
    });
    const response = new Response(body, { status: 423 });
    await handleSessionResponse(response, authGeneration());

    expect(authState()).not.toBeNull();
    // And the screen can still read the message it has to show.
    expect(await response.text()).toBe(body);
  });
});

describe('dispatched session ownership', () => {
  afterEach(() => clearAuthState());

  it('ignores a delayed 401 after the same token is installed as a new session', async () => {
    signIn();
    const outgoing = authGeneration();
    clearAuthState();
    signIn();

    await handleSessionResponse(new Response(null, { status: 401 }), outgoing);

    expect(authState()?.accessToken).toBe('token-123');
  });

  it('rechecks ownership when a 423 body resolves after sign-in', async () => {
    signIn();
    let body!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(new ReadableStream({ start: (controller) => { body = controller; } }), {
      status: 423,
    });
    const rejected = handleSessionResponse(response, authGeneration());
    signIn();

    body.enqueue(new TextEncoder().encode('{"error":{"code":"household_locked"}}'));
    body.close();
    await rejected;

    expect(authState()).not.toBeNull();
    expect(await response.text()).toContain('household_locked');
  });

  it('ignores a failure dispatched without an authenticated owner', async () => {
    signIn();
    await handleSessionResponse(new Response(null, { status: 401 }));
    expect(authState()).not.toBeNull();
  });
});

describe('direct protected fetch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthState();
  });

  it('applies owned 401 handling to a direct export request', async () => {
    signIn();
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);

    const response = await fetchProtected('/api/v1/household/export');

    expect(response.status).toBe(401);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer token-123');
    expect(authState()).toBeNull();
  });
});
