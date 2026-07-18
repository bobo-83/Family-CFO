import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Devices } from './devices';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

const QR_PAYLOAD = JSON.stringify({
  type: 'family-cfo-pairing',
  api_base_url: 'https://192.168.1.10:8443/api/v1',
  pairing_session_id: 'secret',
  certificate_sha256: 'ab'.repeat(32),
});

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Devices],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Devices', () => {
  it('lists devices and renders the pairing QR with the pinned fingerprint (M83a)', async () => {
    const apiMock = {
      listMembers: vi.fn().mockResolvedValue(response({ members: [] })),
      listPairedDevices: vi.fn().mockResolvedValue(
        response({
          devices: [
            {
              id: 'd1',
              name: "Alex's iPhone",
              created_at: '2026-07-12T10:00:00Z',
              last_seen_at: null,
              revoked_at: null,
            },
          ],
        }),
      ),
      createPairingSession: vi.fn().mockResolvedValue(
        response({ id: 's1', qr_payload: QR_PAYLOAD, expires_at: '2026-07-12T10:10:00Z' }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Devices);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain("Alex's iPhone");

    (host.querySelector('.pairing-card button') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(host.querySelector('.pairing-card__qr svg')).not.toBeNull();
    });
    // The fingerprint the iPhone will pin is shown for manual verification.
    expect(host.textContent).toContain('ab'.repeat(32));
    expect(apiMock.createPairingSession).toHaveBeenCalledOnce();
  });

  it('viewer sees the device list but no pairing card, and cannot revoke', async () => {
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
    // The OTA install card shares the pairing-card class and shows for every
    // role; only the QR *pairing* card must be hidden from a viewer.
    expect(host.querySelector('.pairing-card:not(.install-card)')).toBeNull();
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
