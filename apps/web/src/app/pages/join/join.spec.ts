import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { clearAuthState, getToken } from '../../core/token-store';
import { Join } from './join';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Join (ADR 0056)', () => {
  afterEach(() => {
    clearAuthState();
    window.location.hash = '';
  });

  function configure(apiMock: Record<string, unknown>) {
    TestBed.configureTestingModule({
      imports: [Join],
      providers: [
        { provide: ApiService, useValue: apiMock },
        // A stub target so the post-join navigateByUrl('/overview') resolves.
        provideRouter([{ path: 'overview', children: [] }]),
      ],
    });
  }

  it('previews the invite from the fragment token and joins with self-chosen credentials', async () => {
    window.location.hash = '#token=secret-token-abc';
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(
        response({
          household_name: 'The Vu Household',
          email: 'sister@example.com',
          role_name: 'User',
          expires_at: '2026-07-28T00:00:00Z',
        }),
      ),
      acceptInvite: vi.fn().mockResolvedValue(
        response({
          access_token: 'fresh-session',
          expires_at: '2026-07-22T00:00:00Z',
          household_id: 'hh-1',
          user_id: 'u-2',
          role: 'adult',
          role_name: 'User',
          rights: ['finances.view'],
        }),
      ),
    };
    configure(apiMock);

    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(apiMock.previewInvite).toHaveBeenCalledWith('secret-token-abc');
    expect(host.textContent).toContain('The Vu Household');
    expect(host.textContent).toContain('sister@example.com');

    const component = fixture.componentInstance;
    component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
    await component['submit']();

    expect(apiMock.acceptInvite).toHaveBeenCalledWith({
      token: 'secret-token-abc',
      password: 'a-strong-pass',
      display_name: 'Sis',
    });
    // Signed in: the session token is in the store.
    expect(getToken()).toBe('fresh-session');
  });

  it('shows a friendly error for an expired/unknown link', async () => {
    window.location.hash = '#token=stale';
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(
        response(undefined, { error: { message: 'This invite link is expired — ask for a new one.' } }),
      ),
    };
    configure(apiMock);

    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('expired');
    expect(host.querySelector('form')).toBeNull();
  });

  it('rejects a link with no token without calling the API', async () => {
    window.location.hash = '';
    const apiMock = { previewInvite: vi.fn() };
    configure(apiMock);

    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.previewInvite).not.toHaveBeenCalled();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('incomplete');
  });
});
