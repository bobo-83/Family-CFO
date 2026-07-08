import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Users } from './users';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Users', () => {
  let apiMock: {
    listPairedDevices: ReturnType<typeof vi.fn>;
    createPairingSession: ReturnType<typeof vi.fn>;
    revokePairedDevice: ReturnType<typeof vi.fn>;
    listMembers: ReturnType<typeof vi.fn>;
    createMember: ReturnType<typeof vi.fn>;
    updateMemberRole: ReturnType<typeof vi.fn>;
    deleteMember: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    apiMock = {
      listPairedDevices: vi.fn(),
      createPairingSession: vi.fn(),
      revokePairedDevice: vi.fn(),
      listMembers: vi.fn().mockResolvedValue(response({ members: [] })),
      createMember: vi.fn(),
      updateMemberRole: vi.fn(),
      deleteMember: vi.fn(),
    };
  });

  it('renders paired devices with a revoke action for an owner', async () => {
    apiMock.listPairedDevices.mockResolvedValue(
      response({
        devices: [
          {
            id: 'd1',
            name: "Alex's iPhone",
            created_at: '2026-01-01T00:00:00Z',
            last_seen_at: null,
            revoked_at: null,
          },
        ],
      }),
    );

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain("Alex's iPhone");
    const revokeButton = (fixture.nativeElement as HTMLElement).querySelector(
      '.device-list__revoke',
    );
    expect(revokeButton).toBeTruthy();
  });

  it('hides the revoke action for a non-owner', async () => {
    apiMock.listPairedDevices.mockResolvedValue(
      response({
        devices: [
          {
            id: 'd1',
            name: "Alex's iPhone",
            created_at: '2026-01-01T00:00:00Z',
            last_seen_at: null,
            revoked_at: null,
          },
        ],
      }),
    );

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'adult' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const revokeButton = (fixture.nativeElement as HTMLElement).querySelector(
      '.device-list__revoke',
    );
    expect(revokeButton).toBeFalsy();
  });

  it('revokes a device and reloads the list', async () => {
    apiMock.listPairedDevices.mockResolvedValue(
      response({
        devices: [
          {
            id: 'd1',
            name: "Alex's iPhone",
            created_at: '2026-01-01T00:00:00Z',
            last_seen_at: null,
            revoked_at: null,
          },
        ],
      }),
    );
    apiMock.revokePairedDevice.mockResolvedValue(response(undefined));

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();

    await fixture.componentInstance['revokeDevice']('d1');
    fixture.detectChanges();
    await fixture.whenStable();

    expect(apiMock.revokePairedDevice).toHaveBeenCalledWith('d1');
    expect(apiMock.listPairedDevices).toHaveBeenCalledTimes(2);
  });

  it('creates a pairing session and renders a QR code for owner/adult', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));
    apiMock.createPairingSession.mockResolvedValue(
      response({
        id: 'session-1',
        qr_payload: '{"type":"family-cfo-pairing"}',
        expires_at: '2026-01-01T00:10:00Z',
      }),
    );

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();

    await fixture.componentInstance['pairDevice']();
    fixture.detectChanges();

    expect(apiMock.createPairingSession).toHaveBeenCalled();
    const session = fixture.componentInstance['pairingSession']();
    expect(session?.qrPayload).toBe('{"type":"family-cfo-pairing"}');
    expect(session?.qrImageDataUrl.startsWith('data:image/')).toBe(true);
  });

  it('adds a household member for an owner', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));
    apiMock.listMembers.mockResolvedValue(response({ members: [] }));
    apiMock.createMember.mockResolvedValue(
      response({
        user_id: 'u2',
        email: 'a@b.com',
        display_name: 'A',
        role: 'adult',
        created_at: '2026-01-01T00:00:00Z',
      }),
    );

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    component['memberForm'].setValue({
      email: 'a@b.com',
      password: 'password-123',
      displayName: 'A',
      role: 'adult',
    });
    await component['addMember']();

    expect(apiMock.createMember).toHaveBeenCalledWith({
      email: 'a@b.com',
      password: 'password-123',
      display_name: 'A',
      role: 'adult',
    });
  });

  it('hides member management for a non-owner', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));
    apiMock.listMembers.mockResolvedValue(response({ members: [] }));

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'adult' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.member-form')).toBeFalsy();
  });

  it('hides pairing for a viewer', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'viewer' } },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Only the household owner or an adult member can pair a new device.');
  });
});
