import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../../core/auth.service';
import { clearAuthState, signInReason, setAuthState } from '../../core/token-store';
import { Login } from './login';

describe('Login', () => {
  let authServiceMock: { login: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    authServiceMock = { login: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [Login],
      providers: [
        provideRouter([{ path: 'login', children: [] }, { path: 'overview', children: [] }]),
        { provide: AuthService, useValue: authServiceMock },
      ],
    }).compileComponents();
  });

  afterEach(() => clearAuthState());

  it('does not submit when the form is invalid', async () => {
    const fixture = TestBed.createComponent(Login);
    const component = fixture.componentInstance;

    await component['submit']();

    expect(authServiceMock.login).not.toHaveBeenCalled();
  });

  it('calls AuthService.login and navigates on success', async () => {
    authServiceMock.login.mockResolvedValue({ ok: true });
    const fixture = TestBed.createComponent(Login);
    const component = fixture.componentInstance;
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    component['form'].setValue({ email: 'demo@family-cfo.local', password: 'demo-password-123' });
    await component['submit']();

    expect(authServiceMock.login).toHaveBeenCalledWith('demo@family-cfo.local', 'demo-password-123');
    expect(navigateSpy).toHaveBeenCalledWith('/overview');
  });

  it('shows the error message and does not navigate on failure', async () => {
    authServiceMock.login.mockResolvedValue({ ok: false, errorMessage: 'Invalid email or password' });
    const fixture = TestBed.createComponent(Login);
    const component = fixture.componentInstance;
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    component['form'].setValue({ email: 'demo@family-cfo.local', password: 'wrong-password' });
    await component['submit']();

    expect(component['errorMessage']()).toBe('Invalid email or password');
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('explains why the sign-in page is asking again', async () => {
    const fixture = TestBed.createComponent(Login);
    const component = fixture.componentInstance;

    signInReason.set('expired');
    expect(component['notice']()).toContain('expired');

    signInReason.set('rejected');
    expect(component['notice']()).toContain('sign in again');

    signInReason.set(null);
    expect(component['notice']()).toBeNull();
  });

  it('leaves the sign-in page when another tab signs in (#164 review)', async () => {
    const fixture = TestBed.createComponent(Login);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/login');
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    // Another tab signs in: the session appears under this tab's feet, and a
    // sign-in page with a live session behind it has nothing left to offer.
    await setAuthState({
      accessToken: 'other-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    });
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(navigateSpy).toHaveBeenCalledWith('/overview', { replaceUrl: true });
  });

  it('navigates once when its own sign-in succeeds (#164 review)', async () => {
    authServiceMock.login.mockImplementation(async () => {
      await setAuthState({
        accessToken: 'own-bearer', expiresAt: '2099-01-01T00:00:00Z',
        householdId: 'household', userId: 'member', role: 'owner',
      });
      return { ok: true };
    });
    const fixture = TestBed.createComponent(Login);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/login');
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    const component = fixture.componentInstance;
    component['form'].setValue({ email: 'demo@family-cfo.local', password: 'demo-password-123' });
    await component['submit']();
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The session that appeared is this tab's own: the page must not redirect
    // on its own as well, or the dashboard would be entered twice.
    expect(navigateSpy.mock.calls.filter((call) => call[0] === '/overview').length).toBe(1);
  });
});
