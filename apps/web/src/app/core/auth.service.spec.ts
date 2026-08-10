import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { clearAuthState } from './token-store';

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
  let apiMock: { login: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    clearAuthState();
    apiMock = { login: vi.fn() };
    TestBed.configureTestingModule({
      providers: [{ provide: ApiService, useValue: apiMock }],
    });
    service = TestBed.inject(AuthService);
  });

  it('starts unauthenticated', () => {
    expect(service.isAuthenticated()).toBe(false);
    expect(service.role()).toBeNull();
  });

  it('stores the session on successful login', async () => {
    apiMock.login.mockResolvedValue(
      response({
        access_token: 'token-abc',
        expires_at: '2026-01-01T00:00:00Z',
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

  it('clears the session on logout', async () => {
    apiMock.login.mockResolvedValue(
      response({
        access_token: 'token-abc',
        expires_at: '2026-01-01T00:00:00Z',
        household_id: 'household-1',
        user_id: 'user-1',
        role: 'owner',
      }),
    );
    await service.login('demo@family-cfo.local', 'demo-password-123');

    service.logout();

    expect(service.isAuthenticated()).toBe(false);
  });
});
