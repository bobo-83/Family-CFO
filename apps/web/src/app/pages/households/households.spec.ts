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

function configure(apiMock: Record<string, unknown>, role: string, householdId?: string) {
  TestBed.configureTestingModule({
    imports: [Households],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role, 'current-user', householdId) },
    ],
  });
}

async function render(apiMock: Record<string, unknown>, role = 'owner', householdId?: string) {
  configure(apiMock, role, householdId);
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

  // --- Delete household (#189) ---

  it('confirms, deletes, and reloads the roster', async () => {
    const apiMock = {
      listHostedHouseholds: vi
        .fn()
        .mockResolvedValueOnce(response({ households: [CEDAR, BIRCH] }))
        .mockResolvedValueOnce(response({ households: [BIRCH] })),
      deleteHostedHousehold: vi.fn().mockResolvedValue(response(undefined)),
    };
    const fixture = await render(apiMock);
    const component = fixture.componentInstance;

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await component['deleteHousehold'](CEDAR);
    // The confirmation NAMES the household and states the consequences.
    expect(confirmSpy).toHaveBeenCalledWith(
      "Permanently delete Cedar family? This removes the family's accounts, " +
        'transactions, advisor history, documents, and logins. It cannot be undone. ' +
        'Their data remains only in whole-box backups until those age out.',
    );
    expect(apiMock.deleteHostedHousehold).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await component['deleteHousehold'](CEDAR);
    confirmSpy.mockRestore();

    expect(apiMock.deleteHostedHousehold).toHaveBeenCalledWith('h-cedar');
    await fixture.whenStable();
    fixture.detectChanges();
    expect(apiMock.listHostedHouseholds).toHaveBeenCalledTimes(2);
    expect((fixture.nativeElement as HTMLElement).textContent).not.toContain('Cedar family');
  });

  it('never offers Delete on the current household', async () => {
    const apiMock = {
      listHostedHouseholds: vi.fn().mockResolvedValue(response({ households: [CEDAR, BIRCH] })),
    };
    const fixture = await render(apiMock, 'owner', 'h-birch');
    const host = fixture.nativeElement as HTMLElement;

    const deleteButtons = host.querySelectorAll('.household-list__delete');
    expect(deleteButtons.length).toBe(1);
    expect(deleteButtons[0].closest('li')?.textContent).toContain('Cedar family');
  });

  it('surfaces the 409 detail verbatim when deleting your own household', async () => {
    const detail = "You can't delete the household you belong to.";
    const apiMock = {
      listHostedHouseholds: vi.fn().mockResolvedValue(response({ households: [CEDAR] })),
      deleteHostedHousehold: vi
        .fn()
        .mockResolvedValue(response(undefined, { error: { message: detail } })),
    };
    const fixture = await render(apiMock);
    const component = fixture.componentInstance;

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await component['deleteHousehold'](CEDAR);
    confirmSpy.mockRestore();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(detail);
    // The roster is not reloaded on failure.
    expect(apiMock.listHostedHouseholds).toHaveBeenCalledTimes(1);
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
