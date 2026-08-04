import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Households } from './households';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

const CEDAR = {
  id: 'h-cedar',
  name: 'Cedar family',
  base_currency: 'USD',
  created_at: '2026-07-01T00:00:00Z',
  member_count: 0,
  pending_owner_invite: true,
  sealed: false,
};

const BIRCH = {
  id: 'h-birch',
  name: 'Birch family',
  base_currency: 'EUR',
  created_at: '2026-05-15T00:00:00Z',
  member_count: 3,
  pending_owner_invite: false,
  sealed: true,
};

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Households],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

async function render(apiMock: Record<string, unknown>, role = 'owner') {
  configure(apiMock, role);
  const fixture = TestBed.createComponent(Households);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture;
}

describe('Households', () => {
  it('lists hosted households with member counts and badges (#180)', async () => {
    const apiMock = {
      listHostedHouseholds: vi
        .fn()
        .mockResolvedValue(response({ households: [CEDAR, BIRCH] })),
    };
    const fixture = await render(apiMock);
    const host = fixture.nativeElement as HTMLElement;

    expect(host.textContent).toContain('Cedar family');
    expect(host.textContent).toContain('Birch family');
    expect(host.textContent).toContain('0 members');
    expect(host.textContent).toContain('3 members');
    expect(host.textContent).toContain('USD');
    expect(host.textContent).toContain('EUR');

    const pending = host.querySelectorAll('.household-badge--pending');
    expect(pending.length).toBe(1);
    expect(pending[0].textContent).toContain('invite pending');
    const sealed = host.querySelectorAll('.household-badge--sealed');
    expect(sealed.length).toBe(1);
    expect(sealed[0].textContent).toContain('sealed');
  });

  it('creates a household and shows the one-time join link, then reloads', async () => {
    const apiMock = {
      listHostedHouseholds: vi
        .fn()
        .mockResolvedValueOnce(response({ households: [] }))
        .mockResolvedValueOnce(response({ households: [CEDAR] })),
      createHostedHousehold: vi.fn().mockResolvedValue(
        response({
          household: CEDAR,
          invite_token: 'one-time-secret',
          invite_expires_at: '2026-07-08T00:00:00Z',
        }),
      ),
    };
    const fixture = await render(apiMock);
    const component = fixture.componentInstance;
    const host = fixture.nativeElement as HTMLElement;

    component['createForm'].setValue({
      displayName: 'Cedar family',
      baseCurrency: 'usd',
      ownerEmail: 'cedar@example.test',
    });
    await component['createHousehold']();
    fixture.detectChanges();

    // The currency is normalized to uppercase before it reaches the server.
    expect(apiMock.createHostedHousehold).toHaveBeenCalledWith({
      display_name: 'Cedar family',
      base_currency: 'USD',
      owner_email: 'cedar@example.test',
    });
    // The one-time link is composed client-side with the token in the FRAGMENT.
    expect(host.textContent).toContain('/join#token=one-time-secret');
    expect(host.textContent).toContain('Shown once — share it with the family now.');
    expect(host.textContent).toContain('cedar@example.test');
    await fixture.whenStable();
    expect(apiMock.listHostedHouseholds).toHaveBeenCalledTimes(2);
  });

  it('surfaces the 409 detail verbatim when the email already has an account', async () => {
    const detail =
      'That email already has an account on this box — invite them from their existing household instead.';
    const apiMock = {
      listHostedHouseholds: vi.fn().mockResolvedValue(response({ households: [] })),
      createHostedHousehold: vi
        .fn()
        .mockResolvedValue(response(undefined, { error: { message: detail } })),
    };
    const fixture = await render(apiMock);
    const component = fixture.componentInstance;

    component['createForm'].setValue({
      displayName: 'Cedar family',
      baseCurrency: 'USD',
      ownerEmail: 'cedar@example.test',
    });
    await component['createHousehold']();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(detail);
    expect(component['inviteLink']()).toBeNull();
  });

  it('hides the page content and never calls the API for a non-admin', async () => {
    const apiMock = {
      listHostedHouseholds: vi.fn().mockResolvedValue(response({ households: [CEDAR] })),
    };
    const fixture = await render(apiMock, 'adult');
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('.create-form')).toBeNull();
    expect(host.querySelector('.household-list')).toBeNull();
    expect(host.textContent).toContain('Only a system administrator');
    expect(apiMock.listHostedHouseholds).not.toHaveBeenCalled();
  });
});
