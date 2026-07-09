import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../../core/auth.service';
import { Signup } from './signup';

describe('Signup', () => {
  let authServiceMock: { signup: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    authServiceMock = { signup: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [Signup],
      providers: [provideRouter([]), { provide: AuthService, useValue: authServiceMock }],
    }).compileComponents();
  });

  const validValue = {
    displayName: 'Vu Family',
    baseCurrency: 'usd',
    ownerDisplayName: 'Alex',
    ownerEmail: 'alex@example.com',
    ownerPassword: 'a-strong-password',
  };

  it('does not submit when the form is invalid', async () => {
    const fixture = TestBed.createComponent(Signup);
    await fixture.componentInstance['submit']();
    expect(authServiceMock.signup).not.toHaveBeenCalled();
  });

  it('creates the household (currency upper-cased) and navigates on success', async () => {
    authServiceMock.signup.mockResolvedValue({ ok: true });
    const fixture = TestBed.createComponent(Signup);
    const component = fixture.componentInstance;
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    component['form'].setValue(validValue);
    await component['submit']();

    expect(authServiceMock.signup).toHaveBeenCalledWith({
      display_name: 'Vu Family',
      base_currency: 'USD',
      owner_display_name: 'Alex',
      owner_email: 'alex@example.com',
      owner_password: 'a-strong-password',
    });
    expect(navigateSpy).toHaveBeenCalledWith('/overview');
  });

  it('surfaces the error and does not navigate on failure', async () => {
    authServiceMock.signup.mockResolvedValue({ ok: false, errorMessage: 'Email already in use' });
    const fixture = TestBed.createComponent(Signup);
    const component = fixture.componentInstance;
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    component['form'].setValue(validValue);
    await component['submit']();

    expect(component['errorMessage']()).toBe('Email already in use');
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
