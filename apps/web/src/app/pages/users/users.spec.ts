import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
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
    listRoles: ReturnType<typeof vi.fn>;
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
      listRoles: vi.fn().mockResolvedValue(
        response({
          roles: [
            { id: 'r-admin', name: 'Admin', rights: [], built_in: true, member_count: 1 },
            { id: 'r-user', name: 'User', rights: [], built_in: true, member_count: 0 },
          ],
          all_rights: [],
        }),
      ),
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
        { provide: AuthService, useValue: authMock('owner') },
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
        { provide: AuthService, useValue: authMock('adult') },
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
        { provide: AuthService, useValue: authMock('owner') },
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

  it('mints a per-member pairing session and renders that member’s QR', async () => {
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
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();

    const member = { user_id: 'u1', email: 'u1@x.com', display_name: 'U1', role: 'adult' };
    await fixture.componentInstance['pairDeviceFor'](member as never);
    fixture.detectChanges();

    // On-behalf pairing targets that member's id.
    expect(apiMock.createPairingSession).toHaveBeenCalledWith('u1');
    const session = fixture.componentInstance['pairingSession']();
    expect(session?.userId).toBe('u1');
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
        { provide: AuthService, useValue: authMock('owner') },
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
      roleId: 'r-user',
    });
    await component['addMember']();

    expect(apiMock.createMember).toHaveBeenCalledWith({
      email: 'a@b.com',
      password: 'password-123',
      display_name: 'A',
      role_id: 'r-user',
    });
  });

  it('hides member management for a non-owner', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));
    apiMock.listMembers.mockResolvedValue(response({ members: [] }));

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('adult') },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.member-form')).toBeFalsy();
  });

  it('lets a viewer pair their own device from their own row (ADR 0034)', async () => {
    apiMock.listPairedDevices.mockResolvedValue(response({ devices: [] }));
    apiMock.listMembers.mockResolvedValue(
      response({
        members: [
          {
            user_id: 'v1',
            email: 'viewer@x.com',
            display_name: 'Viewer',
            role: 'viewer',
            role_id: 'r-viewer',
            created_at: '2026-01-01T00:00:00Z',
          },
        ],
      }),
    );

    TestBed.configureTestingModule({
      imports: [Users],
      providers: [
        { provide: ApiService, useValue: apiMock },
        // The viewer IS member v1; their own row auto-expands so they can self-pair.
        { provide: AuthService, useValue: authMock('viewer', 'v1') },
      ],
    });
    const fixture = TestBed.createComponent(Users);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    // ADR 0034: pairing your OWN device needs only membership.
    expect(text).not.toContain('Only an admin can pair a device for another member.');
    expect(text).toContain('Pair a device');
  });
});
