import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../../core/auth.service';
import { Login } from './login';

describe('Login', () => {
  let authServiceMock: { login: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    authServiceMock = { login: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [Login],
      providers: [provideRouter([]), { provide: AuthService, useValue: authServiceMock }],
    }).compileComponents();
  });

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
});
