import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { clearAuthState } from './token-store';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
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
