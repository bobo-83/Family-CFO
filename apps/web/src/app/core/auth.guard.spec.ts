import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, UrlTree } from '@angular/router';
import { authGuard } from './auth.guard';
import { clearAuthState, setAuthState } from './token-store';

describe('authGuard', () => {
  beforeEach(() => {
    clearAuthState();
    TestBed.configureTestingModule({
      providers: [provideRouter([])],
    });
  });

  it('allows navigation when authenticated', () => {
    setAuthState({ accessToken: 't', expiresAt: '2099-01-01T00:00:00Z', householdId: 'h', userId: 'u', role: 'owner' });

    const result = TestBed.runInInjectionContext(() =>
      authGuard({} as never, { url: '/overview' } as never),
    );

    expect(result).toBe(true);
  });

  it('redirects to /login when unauthenticated', () => {
    const result = TestBed.runInInjectionContext(() =>
      authGuard({} as never, { url: '/overview' } as never),
    );

    expect(result).toBeInstanceOf(UrlTree);
    const router = TestBed.inject(Router);
    expect(router.serializeUrl(result as UrlTree)).toBe('/login');
  });
});
