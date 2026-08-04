import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Backups } from './backups';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

// ADR 0072 Phase 2 fixture: convenient mode with a recovery key already minted.
function keyStatus(overrides: Record<string, unknown> = {}) {
  return {
    encryption_enabled: true,
    member_wraps: 2,
    device_wraps: 3,
    has_recovery_key: true,
    recovery_key_created_at: '2026-07-01T12:00:00Z',
    ...overrides,
  };
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Backups],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

describe('Backups', () => {
  it('hides everything for a non-owner', async () => {
    const apiMock = { listBackups: vi.fn().mockResolvedValue(response({ backups: [] })) };
    configure(apiMock, 'adult');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.backups-actions')).toBeFalsy();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Only the household owner',
    );
  });

  it('creates a backup for an owner', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      createBackup: vi.fn().mockResolvedValue(response({ id: 'b1', status: 'completed' })),
      // M98: a fresh backup also refreshes the Synology (remote) list.
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    await fixture.componentInstance['createBackup']();
    expect(apiMock.createBackup).toHaveBeenCalled();
  });

  it('confirms before restoring', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      restoreBackup: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await fixture.componentInstance['restore']('b1');
    expect(confirmSpy).toHaveBeenCalled();
    expect(apiMock.restoreBackup).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await fixture.componentInstance['restore']('b1');
    expect(apiMock.restoreBackup).toHaveBeenCalledWith('b1');
    confirmSpy.mockRestore();
  });

  it('renders the household key status', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Restore keys');
    expect(text).toContain('Restoring onto a new box takes both keys');
    expect(text).toContain('1 · Backup key');
    expect(text).toContain('Opens your backup files.');
    expect(text).toContain('2 · Recovery key');
    expect(text).toContain('Unlocks the content inside');
    expect(text).toContain('Content encrypted per household');
    expect(text).toContain('2 member keys, 3 device keys');
    expect(text).toContain('Recovery key created');
    expect(text).toContain('Replace recovery key');
    // The old section headers are gone.
    expect(text).not.toContain('Encryption key');
    expect(text).not.toContain('Data encryption');
  });

  it('nudges to create a recovery key when there is none', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(
        response(keyStatus({ has_recovery_key: false, recovery_key_created_at: null })),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('No recovery key yet.');
    expect(text).toContain('Create recovery key');
    expect(text).not.toContain('Replace recovery key');
  });

  it('generates a recovery key and shows it once', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValueOnce(
          response(keyStatus({ has_recovery_key: false, recovery_key_created_at: null })),
        )
        .mockResolvedValue(response(keyStatus())),
      generateRecoveryKey: vi
        .fn()
        .mockResolvedValue(response({ recovery_key: 'FCFO-test-recovery-key-0000' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    // No key yet — no replace confirmation should be involved.
    await fixture.componentInstance['generateRecoveryKey']();
    fixture.detectChanges();

    expect(apiMock.generateRecoveryKey).toHaveBeenCalled();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('FCFO-test-recovery-key-0000');
    expect(text).toContain('This is the only time it will be shown.');
  });

  it('hides the recovery key blocks when encryption is off', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(
        response(keyStatus({ encryption_enabled: false, has_recovery_key: false })),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Per-household encryption is off on this box.');
    expect(text).not.toContain('Create recovery key');
    expect(text).not.toContain('Content encrypted per household');
    // The backup-key step still renders.
    expect(text).toContain('1 · Backup key');
  });
});
