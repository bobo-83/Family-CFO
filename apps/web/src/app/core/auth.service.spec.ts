import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { authState, clearAuthState, setAuthState } from './token-store';

function response(data: unknown, error?: unknown, raw = new Response()) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: raw,
  } as never;
}

/** The brute-force lockout's answer: a generic body, the real wait in the header. */
function rateLimited(retryAfter?: string) {
  const raw = new Response(null, {
    status: 429,
    headers: retryAfter ? { 'Retry-After': retryAfter } : {},
  });
  return response(
    undefined,
    { error: { code: 'http_error', message: 'Too many login attempts. Try again later.' } },
    raw,
  );
}

describe('AuthService', () => {
  let service: AuthService;
  let apiMock: {
    login: ReturnType<typeof vi.fn>;
    getSessionInfo: ReturnType<typeof vi.fn>;
    createHousehold: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    clearAuthState();
    localStorage.clear();
    apiMock = { login: vi.fn(), getSessionInfo: vi.fn(), createHousehold: vi.fn() };
    TestBed.configureTestingModule({
      providers: [{ provide: ApiService, useValue: apiMock }],
    });
    service = TestBed.inject(AuthService);
  });

  it('starts unauthenticated', () => {
    expect(service.isAuthenticated()).toBe(false);
    expect(service.role()).toBeNull();
    expect(service.userId()).toBeNull();
    expect(service.householdId()).toBeNull();
    expect(service.roleName()).toBeNull();
  });

  it('stores the session on successful login', async () => {
    apiMock.login.mockResolvedValue(
      response({
        access_token: 'token-abc',
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        household_id: 'household-1',
        user_id: 'user-1',
        role: 'owner',
      }),
    );

    const result = await service.login('demo@family-cfo.local', 'demo-password-123');

    expect(result.ok).toBe(true);
    expect(service.isAuthenticated()).toBe(true);
    expect(service.role()).toBe('owner');
    expect(service.householdId()).toBe('household-1');
  });

  it('surfaces the API error message on failed login', async () => {
    apiMock.login.mockResolvedValue(
      response(undefined, { error: { code: 'http_error', message: 'Invalid email or password' } }),
    );

    const result = await service.login('demo@family-cfo.local', 'wrong-password');

    expect(result.ok).toBe(false);
    expect(result.errorMessage).toBe('Invalid email or password');
    expect(service.isAuthenticated()).toBe(false);
  });

  // #92: the load-bearing property is that the sentence follows the header.
  // Asserting one fixed sentence is what let "wait a minute" survive a
  // fifteen-minute lockout.
  it('names the lockout the server sent, not a constant', async () => {
    apiMock.login.mockResolvedValue(rateLimited('883'));
    const quarterHour = await service.login('demo@family-cfo.local', 'wrong-password');

    apiMock.login.mockResolvedValue(rateLimited('120'));
    const twoMinutes = await service.login('demo@family-cfo.local', 'wrong-password');

    expect(quarterHour.errorMessage).toBe('Too many attempts — try again in 15 minutes.');
    expect(twoMinutes.errorMessage).toBe('Too many attempts — try again in 2 minutes.');
    expect(quarterHour.errorMessage).not.toBe(twoMinutes.errorMessage);
  });

  it('names no duration when the box or a proxy sent no Retry-After', async () => {
    apiMock.login.mockResolvedValue(rateLimited());
    const absent = await service.login('demo@family-cfo.local', 'wrong-password');

    apiMock.login.mockResolvedValue(rateLimited('not-a-number'));
    const unparseable = await service.login('demo@family-cfo.local', 'wrong-password');

    expect(absent.errorMessage).toBe('Too many attempts — try again later.');
    expect(unparseable.errorMessage).toBe('Too many attempts — try again later.');
    expect(absent.errorMessage).not.toMatch(/\d/);
  });

  it('discards a late rights response after same-token relogin', async () => {
    const session = {
      accessToken: 'same-synthetic-token', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'adult', rights: ['finances.view'],
    };
    let complete!: (value: unknown) => void;
    apiMock.getSessionInfo.mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    setAuthState(session);
    const refreshing = service.refreshRights();
    service.logout();
    setAuthState(session);
    complete(response({ role: 'owner', rights: ['backups.manage'] }));
    await refreshing;
    expect(authState()?.rights).toEqual(['finances.view']);
  });

  it('refuses an older login whose shared pointer moved while in flight (#164)', async () => {
    let complete!: (value: unknown) => void;
    apiMock.login.mockReturnValue(new Promise((resolve) => (complete = resolve)));
    const signingIn = service.login('user@example.test', 'synthetic-password');

    // Another tab installs a newer shared session while this request is in
    // flight, and its storage event has not been delivered to this tab — the
    // local generation check cannot see it. The pointer fence must.
    const newerOwner = '00000000000000000000000000000164';
    localStorage.setItem('family-cfo-auth-session:' + newerOwner, JSON.stringify({
      accessToken: 'newer-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'newer-household', userId: 'newer-user', role: 'owner',
    }));
    localStorage.setItem('family-cfo-auth-owner', newerOwner);

    complete(response({
      access_token: 'stale-bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      household_id: 'stale-household',
      user_id: 'stale-user',
      role: 'owner',
    }));
    expect((await signingIn).ok).toBe(false);

    expect(authState()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(newerOwner);
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
  });

  it('refuses an older signup whose shared pointer moved while in flight (#164)', async () => {
    let complete!: (value: unknown) => void;
    apiMock.createHousehold.mockReturnValue(new Promise((resolve) => (complete = resolve)));
    const signingUp = service.signup({} as never);
    const newerOwner = '00000000000000000000000000000165';
    localStorage.setItem('family-cfo-auth-session:' + newerOwner, JSON.stringify({
      accessToken: 'newer-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'newer-household', userId: 'newer-user', role: 'owner',
    }));
    localStorage.setItem('family-cfo-auth-owner', newerOwner);
    complete(response({
      access_token: 'stale-bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      household_id: 'stale-household',
      user_id: 'stale-user',
      role: 'owner',
    }));

    expect((await signingUp).ok).toBe(false);
    expect(authState()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
  });

  it('clears the session on logout', async () => {
    apiMock.login.mockResolvedValue(
      response({
        access_token: 'token-abc',
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        household_id: 'household-1',
        user_id: 'user-1',
        role: 'owner',
      }),
    );
    await service.login('demo@family-cfo.local', 'demo-password-123');

    service.logout();

    expect(service.isAuthenticated()).toBe(false);
  });

  it('exposes identity computeds and right-gates by the stored rights list', () => {
    setAuthState({
      accessToken: 't', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'h', userId: 'member', role: 'owner',
      roleName: 'Owner', rights: ['finances.view'],
    });
    expect(service.userId()).toBe('member');
    expect(service.roleName()).toBe('Owner');
    expect(service.hasRight('finances.view')).toBe(true);
    expect(service.hasRight('backups.manage')).toBe(false);
    clearAuthState();
    expect(service.hasRight('finances.view')).toBe(false);
  });

  it('falls back to the legacy role tiers when a stored session carries no rights', () => {
    setAuthState({
      accessToken: 't', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'h', userId: 'member', role: 'owner',
    });
    expect(service.hasRight('system.admin')).toBe(true);
    setAuthState({
      accessToken: 't2', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'h', userId: 'member', role: 'adult',
    });
    expect(service.hasRight('finances.view')).toBe(true);
    expect(service.hasRight('members.manage')).toBe(false);
    setAuthState({
      accessToken: 't3', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'h', userId: 'member', role: 'viewer',
    });
    expect(service.hasRight('advisor.use')).toBe(true);
    expect(service.hasRight('audit.view')).toBe(false);
  });

  it('refreshRights is inert without a session or on a failed fetch', async () => {
    await service.refreshRights();
    expect(apiMock.getSessionInfo).not.toHaveBeenCalled();

    setAuthState({
      accessToken: 't', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'h', userId: 'member', role: 'adult', rights: ['finances.view'],
    });
    apiMock.getSessionInfo.mockResolvedValue(response(undefined, { code: 'network' }));
    await service.refreshRights();
    expect(authState()?.rights).toEqual(['finances.view']);
  });

  it('reports a failed signup without installing a session', async () => {
    apiMock.createHousehold.mockResolvedValue(response(undefined, { code: 'email_taken' }));
    const result = await service.signup({} as never);
    expect(result.ok).toBe(false);
    expect(result.errorMessage).toBeTruthy();
    expect(authState()).toBeNull();
  });

  it('discards a login whose generation was superseded while in flight', async () => {
    let complete!: (value: unknown) => void;
    apiMock.login.mockReturnValue(new Promise((resolve) => (complete = resolve)));
    const signingIn = service.login('user@example.test', 'synthetic-password');
    clearAuthState(); // bumps this tab's generation — the response is now stale
    complete(response({
      access_token: 'late-bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      household_id: 'h', user_id: 'u', role: 'owner',
    }));
    expect((await signingIn).ok).toBe(false);
    expect(authState()).toBeNull();
  });

  it('discards a signup whose generation was superseded while in flight', async () => {
    let complete!: (value: unknown) => void;
    apiMock.createHousehold.mockReturnValue(new Promise((resolve) => (complete = resolve)));
    const signingUp = service.signup({} as never);
    clearAuthState();
    complete(response({
      access_token: 'late-bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      household_id: 'h', user_id: 'u', role: 'owner',
    }));
    expect((await signingUp).ok).toBe(false);
    expect(authState()).toBeNull();
  });
});
