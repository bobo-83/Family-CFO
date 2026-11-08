import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { clearAuthState, getToken } from '../../core/token-store';
import { Join } from './join';
import { TIMEZONE_BOX_DEFAULT } from '../../shared/timezones';

function response(data: unknown, error?: unknown, raw = new Response()) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: raw,
  } as never;
}

/** The join page shares the auth limiter's 429; the wait lives in the header. */
function rateLimited(retryAfter: string) {
  return response(
    undefined,
    { error: { message: 'Too many attempts. Try again later.' } },
    new Response(null, { status: 429, headers: { 'Retry-After': retryAfter } }),
  );
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
          household_name: 'The Placeholder Household',
          email: 'sister@example.com',
          role_name: 'User',
          expires_at: '2026-07-28T00:00:00Z',
        }),
      ),
      acceptInvite: vi.fn().mockResolvedValue(
        response({
          access_token: 'fresh-session',
          expires_at: new Date(Date.now() + 3_600_000).toISOString(),
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
    expect(host.textContent).toContain('The Placeholder Household');
    expect(host.textContent).toContain('sister@example.com');

    const component = fixture.componentInstance;
    component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
    // #93: pre-filled from the browser; this pins the value the test asserts.
    component['timezone'].set('Europe/London');
    await component['submit']();

    expect(apiMock.acceptInvite).toHaveBeenCalledWith({
      token: 'secret-token-abc',
      password: 'a-strong-pass',
      display_name: 'Sis',
      timezone: 'Europe/London',
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

  // #92: the join page is behind the same lockout as login, so it has to say
  // the same true thing — and say a different thing when the server does.
  it('says how long the join page is locked for, from the header', async () => {
    async function lockedMessage(retryAfter: string): Promise<string> {
      window.location.hash = '#token=stale';
      TestBed.resetTestingModule();
      configure({ previewInvite: vi.fn().mockResolvedValue(rateLimited(retryAfter)) });
      const fixture = TestBed.createComponent(Join);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return (fixture.nativeElement as HTMLElement).textContent ?? '';
    }

    expect(await lockedMessage('900')).toContain('try again in 15 minutes');
    expect(await lockedMessage('300')).toContain('try again in 5 minutes');
  });

  // #103: a sealed household whose key is not readable cannot take a new
  // member, and the invitee is the one person who cannot fix that — they are
  // not a member, so they have nothing to sign in to. The server says who can,
  // and the page has to show THAT rather than its own "link may have expired"
  // guess, which would send them chasing a new link that will fail identically.
  it('shows the server’s wording when the household is locked', async () => {
    window.location.hash = '#token=secret-token-abc';
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(
        response({
          household_name: 'The Placeholder Household',
          email: 'sister@example.com',
          role_name: 'User',
          expires_at: '2026-07-28T00:00:00Z',
        }),
      ),
      acceptInvite: vi.fn().mockResolvedValue(
        response(
          undefined,
          {
            error: {
              code: 'household_locked_new_member',
              message:
                'This household is locked. Ask whoever invited you to sign in, then open this link again.',
            },
          },
          new Response(null, { status: 423 }),
        ),
      ),
    };
    configure(apiMock);

    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
    await component['submit']();
    fixture.detectChanges();

    const shown = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(shown).toContain('Ask whoever invited you to sign in');
    // Not the generic fallback, which names a cause that is not the cause.
    expect(shown).not.toContain('may have expired');
    // The form stays put: they will submit it again after someone signs in.
    expect(fixture.nativeElement.querySelector('form')).not.toBeNull();
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
  // #93: where the person joining actually is. The household's zone decides
  // what "today" means, and acceptance is the only moment they are here to ask.
  describe('time zone (#93)', () => {
    function joinable() {
      window.location.hash = '#token=secret-token-abc';
      return {
        previewInvite: vi.fn().mockResolvedValue(
          response({
            household_name: 'The Placeholder Household',
            email: 'member@example.com',
            role_name: 'User',
            expires_at: '2026-07-28T00:00:00Z',
          }),
        ),
        acceptInvite: vi.fn().mockResolvedValue(
          response({
            access_token: 'fresh-session',
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
            household_id: 'hh-1',
            user_id: 'u-2',
            role: 'adult',
            rights: [],
          }),
        ),
      };
    }

    async function render(apiMock: Record<string, unknown>) {
      configure(apiMock);
      const fixture = TestBed.createComponent(Join);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('offers the picker, pre-filled from this browser', async () => {
      const apiMock = joinable();
      const fixture = await render(apiMock);

      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.join__timezone')).toBeTruthy();
      expect(host.textContent).toContain(
        'Bills, due dates and Safe to Spend use this zone to decide what \u201Ctoday\u201D means.',
      );
      // Confirming a guess, not filling an empty field.
      expect(fixture.componentInstance['timezone']()).toBeTruthy();
    });

    it('sends nothing when they choose the box\'s own zone', async () => {
      const apiMock = joinable();
      const fixture = await render(apiMock);
      const component = fixture.componentInstance;

      component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
      component['pickTimezone'](TIMEZONE_BOX_DEFAULT);
      await component['submit']();

      // Omitted, not null: null would still be a value the server has to read,
      // and today's behaviour is the field simply not being there.
      expect(apiMock.acceptInvite).toHaveBeenCalledWith({
        token: 'secret-token-abc',
        password: 'a-strong-pass',
        display_name: 'Sis',
      });
    });

    it('carries a zone picked from the list', async () => {
      const apiMock = joinable();
      const fixture = await render(apiMock);
      const component = fixture.componentInstance;

      component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
      component['pickTimezone']('Pacific/Kiritimati');
      await component['submit']();

      expect(apiMock.acceptInvite).toHaveBeenCalledWith(
        expect.objectContaining({ timezone: 'Pacific/Kiritimati' }),
      );
    });
  });
});

describe('Join install guards (#164 review)', () => {
  afterEach(() => {
    clearAuthState();
    localStorage.clear();
    window.location.hash = '';
  });

  const preview = {
    household_name: 'The Placeholder Household',
    email: 'sister@example.com',
    role_name: 'User',
    expires_at: '2026-7-28T00:00:00Z',
  };

  // Same wiring as the main describe's helper, which is out of scope here.
  function configure(apiMock: Record<string, unknown>) {
    TestBed.configureTestingModule({
      imports: [Join],
      providers: [
        { provide: ApiService, useValue: apiMock },
        provideRouter([{ path: 'overview', children: [] }]),
      ],
    });
  }

  it('refuses to submit an incomplete form and never calls the API', async () => {
    window.location.hash = '#token=guarded';
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(response(preview)),
      acceptInvite: vi.fn(),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    await component['submit']();

    expect(apiMock.acceptInvite).not.toHaveBeenCalled();
    expect(component['submitting']()).toBe(false);
    expect(component['form'].get('displayName')?.touched).toBe(true);
  });

  it('refuses an older acceptance whose shared pointer moved while in flight', async () => {
    window.location.hash = '#token=raced';
    let complete!: (value: unknown) => void;
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(response(preview)),
      acceptInvite: vi.fn().mockReturnValue(new Promise((resolve) => (complete = resolve))),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    const navigate = vi.spyOn(component['router'], 'navigateByUrl');
    component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
    const accepting = component['submit']();

    // Another tab signs in while this acceptance is in flight.
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
      role: 'adult',
    }));
    await accepting;

    expect(getToken()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(newerOwner);
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('Join generation fencing (#164)', () => {
  afterEach(() => {
    clearAuthState();
    localStorage.clear();
    window.location.hash = '';
  });

  it('discards an acceptance whose tab generation was superseded in flight', async () => {
    window.location.hash = '#token/stale-generation';
    let complete!: (value: unknown) => void;
    const apiMock = {
      previewInvite: vi.fn().mockResolvedValue(response({
        household_name: 'The Placeholder Household', email: 'sister@example.com',
        role_name: 'User', expires_at: '2026-7-28T00:00:00Z',
      })),
      acceptInvite: vi.fn().mockReturnValue(new Promise((resolve) => (complete = resolve))),
    };
    TestBed.configureTestingModule({
      imports: [Join],
      providers: [
        { provide: ApiService, useValue: apiMock },
        provideRouter([{ path: 'overview', children: [] }]),
      ],
    });
    const fixture = TestBed.createComponent(Join);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    const navigate = vi.spyOn(component['router'], 'navigateByUrl');
    component['form'].setValue({ displayName: 'Sis', password: 'a-strong-pass' });
    const accepting = component['submit']();
    clearAuthState(); // bumps this tab's generation — the response is now stale
    complete(response({
      access_token: 'late-bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      household_id: 'hh', user_id: 'u', role: 'adult',
    }));
    await accepting;

    expect(getToken()).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });
});
