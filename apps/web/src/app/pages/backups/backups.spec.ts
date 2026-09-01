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
    mode: 'convenient',
    unlocked: true,
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

  // --- ADR 0072 Phase 3: privacy mode (convenient ↔ sealed) ---

  it('shows the convenient-mode claim and the seal action', async () => {
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
    expect(text).toContain('Privacy mode');
    expect(text).toContain('Convenient');
    expect(text).toContain('The box keeps a spare of your data key');
    expect(text).toContain('the box itself can still read it');
    expect(text).toContain('Seal this household…');
    // Never overstate: the sealed claim only appears in sealed mode (ADR 0070).
    expect(text).not.toContain('Only your passwords, your phones, and your recovery key');
    expect(text).not.toContain('Locked — sign in again to unlock');
  });

  it('shows the sealed-mode claim, and the locked line when locked', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'sealed', unlocked: false }))),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Sealed');
    expect(text).toContain('Only your passwords, your phones, and your recovery key');
    // #115: the sealed note must not imply the work merely waits — it never
    // runs while sealed, because the worker cannot see the API's session key.
    expect(text).toContain('does not run at all while you are sealed');
    expect(text).toContain('Encrypted backups still run');
    expect(text).not.toContain('waits for you');
    expect(text).toContain('Switch back to convenient…');
    expect(text).toContain('Locked — sign in again to unlock');
    // The rescue lives right beneath the locked line.
    expect(text).toContain('Unlock with recovery key…');
    expect(text).not.toContain('The box keeps a spare of your data key');
  });

  // --- "Unlock with recovery key…" — the rescue for a locked household ---

  it('unlocks with a trimmed recovery key and replaces the status', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'sealed', unlocked: false }))),
      unlockWithRecoveryKey: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'sealed', unlocked: true }))),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    fixture.componentInstance['showRecoveryUnlock'].set(true);
    fixture.componentInstance['recoveryUnlockInput'].set('  FCFO-test-recovery-key-0000  ');
    await fixture.componentInstance['unlockWithRecoveryKey']();
    fixture.detectChanges();

    // Whitespace is trimmed before the key ever leaves the page.
    expect(apiMock.unlockWithRecoveryKey).toHaveBeenCalledWith('FCFO-test-recovery-key-0000');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Household unlocked.');
    expect(text).not.toContain('Locked — sign in again to unlock');
    expect(text).not.toContain('Unlock with recovery key…');
  });

  it('shows the 400 detail verbatim and keeps the input open', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'sealed', unlocked: false }))),
      unlockWithRecoveryKey: vi.fn().mockResolvedValue(
        response(undefined, {
          error: {
            code: 'recovery_key_mismatch',
            message: "That recovery key doesn't match this household.",
          },
        }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    fixture.componentInstance['showRecoveryUnlock'].set(true);
    fixture.componentInstance['recoveryUnlockInput'].set('FCFO-wrong');
    await fixture.componentInstance['unlockWithRecoveryKey']();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain("That recovery key doesn't match this household.");
    // Still locked, input still open for another try.
    expect(host.textContent).toContain('Locked — sign in again to unlock');
    expect(host.querySelector('input[placeholder="FCFO-…"]')).toBeTruthy();
  });

  it('offers the recovery unlock for a locked convenient household too', async () => {
    // A convenient household restored without its master key is locked as
    // well (stale box wrap) — the same rescue applies.
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'convenient', unlocked: false }))),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Convenient');
    expect(text).toContain('Unlock with recovery key…');
  });

  it('confirms before sealing and refreshes the key status', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      setSealMode: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'sealed', unlocked: true }))),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await fixture.componentInstance['setSealMode']('sealed');
    expect(apiMock.setSealMode).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await fixture.componentInstance['setSealMode']('sealed');
    expect(apiMock.setSealMode).toHaveBeenCalledWith('sealed');
    confirmSpy.mockRestore();

    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Switch back to convenient…');
  });

  it('surfaces the 409 precondition message verbatim', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      setSealMode: vi.fn().mockResolvedValue(
        response(undefined, {
          error: {
            code: 'seal_preconditions',
            message: 'Sealing needs at least one member key and a recovery key.',
          },
        }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await fixture.componentInstance['setSealMode']('sealed');
    confirmSpy.mockRestore();

    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Sealing needs at least one member key and a recovery key.',
    );
  });

  // --- "Export my data" (#189) ---

  it('downloads the export zip through the direct fetch path', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      downloadHouseholdExport: vi.fn().mockResolvedValue({
        blob: new Blob(['zip-bytes'], { type: 'application/zip' }),
        filename: 'family-cfo-export-2026-07-26.zip',
      }),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Download everything in this household — accounts, transactions, advisor history, and documents — as a zip you can keep or take elsewhere.',
    );

    // jsdom has no createObjectURL — stub the browser download plumbing.
    URL.createObjectURL = vi.fn(() => 'blob:mock');
    URL.revokeObjectURL = vi.fn();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    await fixture.componentInstance['exportData']();
    fixture.detectChanges();

    expect(apiMock.downloadHouseholdExport).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock');
    expect(fixture.componentInstance['exportError']()).toBeNull();
    clickSpy.mockRestore();
  });

  it('surfaces the 423 locked message inline when the export fails', async () => {
    const detail = "This household's data is sealed and currently locked. Sign in to unlock it.";
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      downloadHouseholdExport: vi.fn().mockRejectedValue(new Error(detail)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    await fixture.componentInstance['exportData']();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(detail);
    expect(fixture.componentInstance['exporting']()).toBe(false);
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
