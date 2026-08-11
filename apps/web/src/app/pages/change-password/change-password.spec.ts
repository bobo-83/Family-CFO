import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { ChangePassword } from './change-password';

function response(status: number, error?: unknown) {
  return {
    data: undefined,
    error,
    request: new Request('http://localhost/'),
    response: new Response(null, { status }),
  } as never;
}

/** #97 shares the auth limiter's 429; the wait lives in the header. */
function rateLimited(retryAfter: string) {
  return {
    data: undefined,
    error: { error: { message: 'Too many attempts. Try again later.' } },
    request: new Request('http://localhost/'),
    response: new Response(null, { status: 429, headers: { 'Retry-After': retryAfter } }),
  } as never;
}

describe('ChangePassword (#97)', () => {
  function configure(apiMock: Record<string, unknown>) {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ChangePassword],
      providers: [{ provide: ApiService, useValue: apiMock }, provideRouter([])],
    });
    return TestBed.createComponent(ChangePassword);
  }

  it('sends both passwords and confirms success without signing the member out', async () => {
    const apiMock = { changePassword: vi.fn().mockResolvedValue(response(204)) };
    const fixture = configure(apiMock);
    const component = fixture.componentInstance;

    component['form'].setValue({
      currentPassword: 'the-current-one',
      newPassword: 'the-replacement',
      confirmPassword: 'the-replacement',
    });
    await component['submit']();
    fixture.detectChanges();

    expect(apiMock.changePassword).toHaveBeenCalledWith({
      current_password: 'the-current-one',
      new_password: 'the-replacement',
    });
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Your password was changed');
    // Still signed in here — the page must not imply otherwise.
    expect(host.textContent).toContain('still signed in here');
  });

  it('does not submit when the new password is too short', async () => {
    const apiMock = { changePassword: vi.fn() };
    const component = configure(apiMock).componentInstance;

    component['form'].setValue({
      currentPassword: 'the-current-one',
      newPassword: 'short',
      confirmPassword: 'short',
    });
    await component['submit']();

    expect(apiMock.changePassword).not.toHaveBeenCalled();
  });

  it('does not submit when the confirmation does not match', async () => {
    const apiMock = { changePassword: vi.fn() };
    const component = configure(apiMock).componentInstance;

    component['form'].setValue({
      currentPassword: 'the-current-one',
      newPassword: 'the-replacement',
      confirmPassword: 'the-replacemant',
    });
    await component['submit']();

    expect(apiMock.changePassword).not.toHaveBeenCalled();
  });

  it('shows the server reason when the current password is wrong', async () => {
    // 403, not 401: a 401 would trip the global dead-session handler and sign
    // the member out over a typo.
    const apiMock = {
      changePassword: vi
        .fn()
        .mockResolvedValue(response(403, { error: { message: 'Current password is incorrect' } })),
    };
    const fixture = configure(apiMock);
    const component = fixture.componentInstance;

    component['form'].setValue({
      currentPassword: 'not-the-one',
      newPassword: 'the-replacement',
      confirmPassword: 'the-replacement',
    });
    await component['submit']();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Current password is incorrect');
    expect(host.textContent).not.toContain('Your password was changed');
  });

  // #92: this form is behind the auth lockout, so it must name the wait rather
  // than guess at it.
  it('says how long the form is locked for, from the header', async () => {
    async function lockedMessage(retryAfter: string): Promise<string> {
      const fixture = configure({
        changePassword: vi.fn().mockResolvedValue(rateLimited(retryAfter)),
      });
      const component = fixture.componentInstance;
      component['form'].setValue({
        currentPassword: 'not-the-one',
        newPassword: 'the-replacement',
        confirmPassword: 'the-replacement',
      });
      await component['submit']();
      fixture.detectChanges();
      return (fixture.nativeElement as HTMLElement).textContent ?? '';
    }

    expect(await lockedMessage('900')).toContain('try again in 15 minutes');
    expect(await lockedMessage('300')).toContain('try again in 5 minutes');
  });
});
