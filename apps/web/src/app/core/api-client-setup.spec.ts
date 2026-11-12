import { vi } from 'vitest';
import { client } from '../api-client/client.gen';
import {
  configureApiClient,
  fetchProtected,
  handleSessionResponse,
} from './api-client-setup';
import {
  authGeneration,
  authState,
  clearAuthState,
  handleSealedHousehold,
  needsKeyRecovery,
  setAuthState,
} from './token-store';

async function signIn() {
  await setAuthState({
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
    await signIn();

    const response = new Response('{}', { status: 200 });
    expect(await handleSessionResponse(response, authGeneration())).toBe(response);

    expect(authState()).not.toBeNull();
  });

  it('drops the session on a 401', async () => {
    await signIn();

    await handleSessionResponse(new Response('{}', { status: 401 }), authGeneration());

    expect(authState()).toBeNull();
  });

  // #101: a 423 is treated EXACTLY like a 401 — session dropped, nothing said.
  // On the web a 423 always means "sign in and this resolves itself", because
  // the password login IS the unlock. Announcing the lock on the login screen
  // read like rejected credentials.
  it('drops the session on a 423 without telling the user anything', async () => {
    await signIn();

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
    await signIn();

    await handleSessionResponse(new Response(null, { status: 423 }), authGeneration());

    expect(authState()).toBeNull();
  });

  // #103: the one 423 that answers an ACTION rather than a stale session —
  // adding a member, or accepting an invite, into a sealed household whose key
  // is not readable. Ending the owner's session here would boot them off the
  // very form whose error message just told them to sign in.
  it('keeps the session on a 423 that refuses a new member', async () => {
    await signIn();

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
    await signIn();
    const outgoing = authGeneration();
    clearAuthState();
    await signIn();

    await handleSessionResponse(new Response(null, { status: 401 }), outgoing);

    expect(authState()?.accessToken).toBe('token-123');
  });

  it('rechecks ownership when a 423 body resolves after sign-in', async () => {
    await signIn();
    let body!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(new ReadableStream({ start: (controller) => { body = controller; } }), {
      status: 423,
    });
    const rejected = handleSessionResponse(response, authGeneration());
    await signIn();

    body.enqueue(new TextEncoder().encode('{"error":{"code":"household_locked"}}'));
    body.close();
    await rejected;

    expect(authState()).not.toBeNull();
    expect(await response.text()).toContain('household_locked');
  });

  it('ignores a failure dispatched without an authenticated owner', async () => {
    await signIn();
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
    await signIn();
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);

    const response = await fetchProtected('/api/v1/household/export');

    expect(response.status).toBe(401);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer token-123');
    expect(authState()).toBeNull();
  });
});

describe('generated-client interceptors', () => {
  afterEach(() => clearAuthState());

  async function roundTrip(url: string, method: string, response: Response): Promise<Response> {
    configureApiClient();
    const requestFn = client.interceptors.request.fns.at(-1) as unknown as (
      request: Request, options: unknown,
    ) => Request | Promise<Request>;
    const responseFn = client.interceptors.response.fns.at(-1) as unknown as (
      response: Response, request: Request, options: unknown,
    ) => Response | Promise<Response>;
    const options = { url, method, security: [{ scheme: 'bearer', type: 'http' }] };
    const request = new Request('http://localhost/api/v1' + url, { method });
    const owned = await requestFn(request, options);
    return responseFn(response, owned, options);
  }

  async function enterKeyRecoveryWindow(): Promise<void> {
    // First sealed 423 signs the session out with no message; the second, in
    // the same household after sign-in, opens the bounded recovery alert.
    await signIn();
    handleSealedHousehold(authGeneration());
    await signIn();
    handleSealedHousehold(authGeneration());
    expect(needsKeyRecovery()).toBe(true);
  }

  it('ends the key-recovery window on a readable GET /household', async () => {
    await enterKeyRecoveryWindow();
    await roundTrip('/household', 'GET', new Response('{}', { status: 200 }));
    expect(needsKeyRecovery()).toBe(false);
  });

  it('keeps the key-recovery window open through an unrelated PATCH /household', async () => {
    await enterKeyRecoveryWindow();
    await roundTrip('/household', 'PATCH', new Response('{}', { status: 200 }));
    expect(needsKeyRecovery()).toBe(true);
    expect(authState()).not.toBeNull();
  });
});

describe('interceptor install guards (#164)', () => {
  afterEach(() => {
    clearAuthState();
    document.documentElement.lang = '';
    vi.unstubAllGlobals();
  });

  function interceptors() {
    configureApiClient();
    return {
      request: client.interceptors.request.fns.at(-1) as unknown as (
        request: Request, options: unknown,
      ) => Request | Promise<Request>,
      response: client.interceptors.response.fns.at(-1) as unknown as (
        response: Response, request: Request, options: unknown,
      ) => Response | Promise<Response>,
    };
  }

  it('leaves public operations unsigned and returns their responses untouched', async () => {
    const { request, response } = interceptors();
    document.documentElement.lang = 'lt';
    const forwarded = await request(new Request('http://localhost/api/v1/health'), {
      url: '/health', method: 'GET',
    });
    expect(forwarded.headers.get('Authorization')).toBeNull();
    expect(forwarded.headers.get('Accept-Language')).toBe('lt');
    const result = await response(new Response('{}', { status: 401 }), forwarded, {
      url: '/health', method: 'GET',
    });
    expect(result.status).toBe(401); // no owner, no session clearing
    expect(authState()).toBeNull();
  });

  it('signs nothing on a secured operation while signed out', async () => {
    const { request, response } = interceptors();
    const options = { url: '/transactions', method: 'GET', security: [{ scheme: 'bearer', type: 'http' }] };
    const unsigned = await request(new Request('http://localhost/api/v1/transactions'), options);
    expect(unsigned.headers.get('Authorization')).toBeNull();
    // No owner was registered, so the response passes through unwatched.
    const result = await response(new Response('{}', { status: 200 }), unsigned, options);
    expect(result.status).toBe(200);
  });

  it('adds the bearer and registers ownership for secured operations', async () => {
    const { request } = interceptors();
    await signIn();
    const secured = await request(new Request('http://localhost/api/v1/transactions', { method: 'GET' }), {
      url: '/transactions', method: 'GET', security: [{ scheme: 'bearer', type: 'http' }],
    });
    expect(secured.headers.get('Authorization')).toBe('Bearer token-123');
  });

  it('throws when a watched request outlives its session', async () => {
    const { request, response } = interceptors();
    await signIn();
    const options = { url: '/transactions', method: 'GET', security: [{ scheme: 'bearer', type: 'http' }] };
    const watched = await request(new Request('http://localhost/api/v1/transactions'), options);
    clearAuthState();
    await expect(response(new Response('{}', { status: 200 }), watched, options)).rejects.toThrow(
      'Session changed',
    );
  });
});

describe('anonymous and superseded direct fetches (#164)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthState();
  });

  it('passes an anonymous direct fetch through untouched', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 423 }));
    vi.stubGlobal('fetch', fetchMock);
    const response = await fetchProtected('/api/v1/household/export');
    expect(response.status).toBe(423);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBeNull();
  });

  it('throws when the session changes while a direct fetch is in flight', async () => {
    await signIn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })));
    const pending = fetchProtected('/api/v1/household/export');
    clearAuthState();
    await expect(pending).rejects.toThrow('Session changed');
  });
});
