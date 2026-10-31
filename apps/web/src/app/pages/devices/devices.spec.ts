import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Devices } from './devices';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Devices],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

describe('Devices', () => {
  it('lists existing devices with account sign-in and installation guidance', async () => {
    const apiMock = {
      listMembers: vi.fn().mockResolvedValue(response({ members: [{ user_id: 'u1', display_name: 'Alex' }] })),
      listPairedDevices: vi.fn().mockResolvedValue(
        response({
          devices: [
            {
              id: 'd1',
              user_id: 'u1',
              name: "Alex's iPhone",
              created_at: '2026-07-12T10:00:00Z',
              last_seen_at: null,
              revoked_at: null,
            },
          ],
        }),
      ),

    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Devices);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain("Alex's iPhone");

    expect(host.textContent).toContain('sign in with your own account');
    expect(host.textContent).toContain('Alex');
    expect(host.querySelector('a[href="/ota/"]')).not.toBeNull();
    expect(host.querySelector('.pairing-card__qr')).toBeNull();
    expect(host.querySelector('textarea')).toBeNull();
  });

  it('viewer gets sign-in guidance but cannot revoke (ADR 0034)', async () => {
    const apiMock = {
      listMembers: vi.fn().mockResolvedValue(response({ members: [] })),
      listPairedDevices: vi.fn().mockResolvedValue(
        response({
          devices: [
            {
              id: 'd1',
              name: 'Old phone',
              created_at: '2026-07-01T10:00:00Z',
              last_seen_at: null,
              revoked_at: '2026-07-10T10:00:00Z',
            },
          ],
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Devices);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('sign in with your own account');
    expect(host.textContent).toContain('revoked');
    expect(host.querySelector('.device-list__revoke')).toBeNull();
  });

  it('owner revokes a device and the list reloads', async () => {
    const apiMock = {
      listMembers: vi.fn().mockResolvedValue(response({ members: [] })),
      listPairedDevices: vi
        .fn()
        .mockResolvedValueOnce(
          response({
            devices: [
              {
                id: 'd1',
                name: 'Lost phone',
                created_at: '2026-07-01T10:00:00Z',
                last_seen_at: null,
                revoked_at: null,
              },
            ],
          }),
        )
        .mockResolvedValueOnce(response({ devices: [] })),
      revokePairedDevice: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Devices);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.device-list__revoke') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.revokePairedDevice).toHaveBeenCalledWith('d1');
    expect(apiMock.listPairedDevices).toHaveBeenCalledTimes(2);
  });
});
