import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { clearAuthState, setAuthState } from '../../core/token-store';
import { authMock } from '../../shared/testing-auth';
import { Backups } from './backups';

function response(data: unknown, error?: unknown, status = 200) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(null, { status }),
  } as never;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
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

function backupConfig(overrides: Record<string, unknown> = {}) {
  return {
    frequency: 'daily',
    smb_host: 'nas.local',
    smb_share: 'backups',
    smb_folder: null,
    smb_username: 'backup-user',
    smb_domain: null,
    has_password: true,
    max_bytes: null,
    local_retention: {
      mode: 'tiered',
      keep_all_days: 3,
      daily_until_days: 14,
      weekly_until_days: 90,
      target_oldest_at: '2026-06-12T00:00:00Z',
    },
    offbox_retention: {
      mode: 'tiered',
      keep_all_days: 7,
      daily_until_days: 30,
      weekly_until_days: 180,
      target_oldest_at: '2026-03-14T00:00:00Z',
    },
    local_max_bytes: 10_000_000_000,
    offbox_max_bytes: 50_000_000_000,
    local_min_free_bytes: 1_000_000_000,
    offbox_min_free_bytes: 2_000_000_000,
    legacy_conflict_detected: false,
    retention_review_required: true,
    retention_activated_at: null,
    revision: 'revision-1',
    updated_at: '2026-09-10T12:00:00Z',
    local_pending_prune_count: 2,
    local_pending_prune_bytes: 3_000_000_000,
    offbox_pending_prune_count: 4,
    offbox_pending_prune_bytes: 5_000_000_000,
    latest: null,
    ...overrides,
  };
}

function capacity(status = 'ok') {
  return {
    status,
    total_bytes: 100_000_000_000,
    available_bytes: 42_000_000_000,
    reserve_bytes: 1_000_000_000,
    estimated_next_backup_bytes: 1_200_000_000,
    can_accept_estimated_backup: status === 'insufficient' ? false : true,
    as_of: '2026-09-10T12:00:00Z',
    reason_code: 'capacity_ok',
    reason: null,
  };
}

function destination(destination: 'local' | 'offbox', overrides: Record<string, unknown> = {}) {
  return {
    destination,
    configured: true,
    status: 'healthy',
    coverage_status: 'met',
    policy:
      destination === 'local' ? backupConfig().local_retention : backupConfig().offbox_retention,
    retention_review_required: false,
    retention_activated_at: '2026-09-10T11:00:00Z',
    pending_prune_count: 0,
    pending_prune_bytes: 0,
    visible_archive_count: 9,
    readable_archive_count: 9,
    probe_status: 'complete',
    probed_archive_count: 2,
    oldest_readable_at: '2026-06-12T00:00:00Z',
    newest_readable_at: '2026-09-10T10:00:00Z',
    oldest_timestamp_source: 'job_started_at',
    metadata_mismatch_count: 0,
    protected_anomaly_count: 0,
    compatibility_unknown_count: 0,
    known_incompatible_count: 0,
    capacity: capacity(),
    reason_codes: [],
    reason: null,
    as_of: '2026-09-10T12:00:00Z',
    verification_scope: 'inventory_read_probe',
    ...overrides,
  };
}

function recoveryStatus(overrides: Record<string, unknown> = {}) {
  return {
    as_of: '2026-09-10T12:00:00Z',
    overall_status: 'healthy',
    overall_oldest_readable_at: '2026-06-12T00:00:00Z',
    overall_newest_readable_at: '2026-09-10T10:00:00Z',
    local: destination('local'),
    offbox: destination('offbox'),
    verification_scope: 'inventory_read_probe',
    ...overrides,
  };
}

type BackupMutation =
  'create' | 'restore-local' | 'restore-remote' | 'delete-local' | 'delete-remote';

const backupMutations: BackupMutation[] = [
  'create',
  'restore-local',
  'restore-remote',
  'delete-local',
  'delete-remote',
];

function invokeMutation(component: Backups, mutation: BackupMutation): Promise<void> {
  switch (mutation) {
    case 'create':
      return component['createBackup']();
    case 'restore-local':
      return component['restore']('local-1');
    case 'restore-remote':
      return component['restoreRemote']('remote-1.tar');
    case 'delete-local':
      return component['deleteLocal']('local-1');
    case 'delete-remote':
      return component['deleteRemote']('remote-1.tar');
  }
}

async function flushUntil(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 20 && !predicate(); attempt++) {
    await Promise.resolve();
  }
  expect(predicate()).toBe(true);
}

function setOwnerSession(suffix: string) {
  setAuthState({
    accessToken: `token-${suffix}`,
    householdId: `household-${suffix}`,
    userId: `user-${suffix}`,
    role: 'owner',
    rights: ['backups.manage'],
  });
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Backups],
    providers: [
      {
        provide: ApiService,
        useValue: {
          getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(undefined, {})),
          ...apiMock,
        },
      },
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
      'Only a system administrator can manage whole-box backups.',
    );
  });

  it('creates a backup for an owner', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
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
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
  });

  it('shows a returned failed backup job as failure and still refreshes state', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      createBackup: vi
        .fn()
        .mockResolvedValue(
          response({ id: 'failed-1', status: 'failed', error_message: 'Disk is full' }),
        ),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const reloadSpy = vi.spyOn(fixture.componentInstance['backups'], 'reload');
    await fixture.componentInstance['createBackup']();

    expect(fixture.componentInstance['latest']()?.status).toBe('failed');
    expect(fixture.componentInstance['statusMessage']()).toBeNull();
    expect(fixture.componentInstance['actionError']()).toContain('Disk is full');
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(apiMock.listRemoteBackups).toHaveBeenCalledTimes(1);
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
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
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(
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

  it('publishes the owned recovery key before status refresh and clears it on A→B', async () => {
    setOwnerSession('recovery-status-a');
    const oldStatus = deferred<ReturnType<typeof response>>();
    const getHouseholdKeyStatus = vi
      .fn()
      .mockResolvedValueOnce(
        response(keyStatus({ has_recovery_key: false, recovery_key_created_at: null })),
      )
      .mockImplementationOnce(() => oldStatus.promise)
      .mockResolvedValue(response(keyStatus({ device_wraps: 9 })));
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus,
      generateRecoveryKey: vi
        .fn()
        .mockResolvedValue(response({ recovery_key: 'owned-one-time-secret' })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    const generation = component['generateRecoveryKey']();
    await flushUntil(() => getHouseholdKeyStatus.mock.calls.length === 2);

    // The only response carrying the secret is not held hostage by the
    // best-effort posture refresh.
    expect(component['generatedRecoveryKey']()).toBe('owned-one-time-secret');
    expect(component['busy']()).toBe(true);

    setOwnerSession('recovery-status-b');
    await flushUntil(() => getHouseholdKeyStatus.mock.calls.length === 3);
    expect(component['generatedRecoveryKey']()).toBeNull();

    oldStatus.resolve(response(keyStatus({ device_wraps: 1 })));
    await generation;
    expect(component['generatedRecoveryKey']()).toBeNull();
    expect(component['keyStatus']()?.device_wraps).toBe(9);
    expect(component['busy']()).toBe(false);
    fixture.destroy();
    clearAuthState();
  });

  it('rejects old-session recovery-key completion without clearing the current request', async () => {
    setOwnerSession('recovery-a');
    const oldRequest = deferred<ReturnType<typeof response>>();
    const currentRequest = deferred<ReturnType<typeof response>>();
    const generateRecoveryKey = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(
          response(keyStatus({ has_recovery_key: false, recovery_key_created_at: null })),
        ),
      generateRecoveryKey,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    const stale = component['generateRecoveryKey']();
    await flushUntil(() => generateRecoveryKey.mock.calls.length === 1);
    setOwnerSession('recovery-b');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length >= 2);
    const current = component['generateRecoveryKey']();
    await flushUntil(() => generateRecoveryKey.mock.calls.length === 2);

    oldRequest.resolve(response({ recovery_key: 'old-session-secret' }));
    await stale;
    expect(component['generatedRecoveryKey']()).toBeNull();
    expect(component['busy']()).toBe(true);
    expect(component['actionError']()).toBeNull();

    currentRequest.resolve(response({ recovery_key: 'current-session-secret' }));
    await current;
    expect(component['generatedRecoveryKey']()).toBe('current-session-secret');
    expect(component['busy']()).toBe(false);
    fixture.destroy();
    clearAuthState();
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
    // #115: the sealed note names the actual in-memory key-session boundary.
    // Auth sessions and this 30-minute sliding TTL are deliberately separate:
    // background reads never extend it, and sign-out does not evict it.
    expect(text).toContain('catches up while the in-memory key session is open');
    expect(text).toContain('expires 30 minutes after its last member-driven use');
    expect(text).toContain('signing out does not close it immediately');
    expect(text).not.toContain('pauses when everyone signs out');
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

  it('rejects old-session unlock completion without clearing replacement-session input', async () => {
    setOwnerSession('unlock-a');
    const oldRequest = deferred<ReturnType<typeof response>>();
    const currentRequest = deferred<ReturnType<typeof response>>();
    const unlockWithRecoveryKey = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(
          response(keyStatus({ mode: 'sealed', unlocked: false, device_wraps: 7 })),
        ),
      unlockWithRecoveryKey,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component['showRecoveryUnlock'].set(true);
    component['recoveryUnlockInput'].set('old-session-key');

    const stale = component['unlockWithRecoveryKey']();
    await flushUntil(() => unlockWithRecoveryKey.mock.calls.length === 1);
    setOwnerSession('unlock-b');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length >= 2);
    component['showRecoveryUnlock'].set(true);
    component['recoveryUnlockInput'].set('current-session-key');
    const current = component['unlockWithRecoveryKey']();
    await flushUntil(() => unlockWithRecoveryKey.mock.calls.length === 2);

    oldRequest.resolve(response(keyStatus({ mode: 'sealed', unlocked: true, device_wraps: 1 })));
    await stale;
    expect(component['recoveryUnlockInput']()).toBe('current-session-key');
    expect(component['showRecoveryUnlock']()).toBe(true);
    expect(component['statusMessage']()).toBeNull();
    expect(component['busy']()).toBe(true);

    currentRequest.resolve(
      response(keyStatus({ mode: 'sealed', unlocked: true, device_wraps: 9 })),
    );
    await current;
    expect(component['recoveryUnlockInput']()).toBe('');
    expect(component['showRecoveryUnlock']()).toBe(false);
    expect(component['statusMessage']()).toBe('Household unlocked.');
    expect(component['keyStatus']()?.device_wraps).toBe(9);
    expect(unlockWithRecoveryKey).toHaveBeenNthCalledWith(1, 'old-session-key');
    expect(unlockWithRecoveryKey).toHaveBeenNthCalledWith(2, 'current-session-key');
    fixture.destroy();
    clearAuthState();
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

  it('rejects old-session seal completion without clearing the current request', async () => {
    setOwnerSession('seal-a');
    const oldRequest = deferred<ReturnType<typeof response>>();
    const currentRequest = deferred<ReturnType<typeof response>>();
    const setSealMode = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(response(keyStatus({ mode: 'convenient', device_wraps: 7 }))),
      setSealMode,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const component = fixture.componentInstance;

    const stale = component['setSealMode']('sealed');
    await flushUntil(() => setSealMode.mock.calls.length === 1);
    setOwnerSession('seal-b');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length >= 2);
    const current = component['setSealMode']('sealed');
    await flushUntil(() => setSealMode.mock.calls.length === 2);

    oldRequest.resolve(response(keyStatus({ mode: 'sealed', device_wraps: 1 })));
    await stale;
    expect(component['keyStatus']()?.mode).toBe('convenient');
    expect(component['keyStatus']()?.device_wraps).toBe(7);
    expect(component['busy']()).toBe(true);

    currentRequest.resolve(response(keyStatus({ mode: 'sealed', device_wraps: 9 })));
    await current;
    expect(component['keyStatus']()?.mode).toBe('sealed');
    expect(component['keyStatus']()?.device_wraps).toBe(9);
    expect(component['busy']()).toBe(false);
    confirmSpy.mockRestore();
    fixture.destroy();
    clearAuthState();
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

  it('does not trigger a download after the component is destroyed', async () => {
    setOwnerSession('export-destroy');
    const request = deferred<{ blob: Blob; filename: string }>();
    const downloadHouseholdExport = vi.fn().mockImplementation(() => request.promise);
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      downloadHouseholdExport,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    URL.createObjectURL = vi.fn(() => 'blob:destroyed');
    URL.revokeObjectURL = vi.fn();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    const exportAction = component['exportData']();
    await flushUntil(() => downloadHouseholdExport.mock.calls.length === 1);
    fixture.destroy();
    request.resolve({
      blob: new Blob(['destroyed-component-bytes'], { type: 'application/zip' }),
      filename: 'destroyed.zip',
    });
    await exportAction;

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
    expect(component['exporting']()).toBe(false);
    clickSpy.mockRestore();
    clearAuthState();
  });

  it('does not download an old-session export or clear the current export slot', async () => {
    setOwnerSession('export-a');
    const oldRequest = deferred<{ blob: Blob; filename: string }>();
    const currentRequest = deferred<{ blob: Blob; filename: string }>();
    const downloadHouseholdExport = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValue(response(backupConfig({ smb_host: null, has_password: false }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      downloadHouseholdExport,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    URL.createObjectURL = vi.fn(() => 'blob:current');
    URL.revokeObjectURL = vi.fn();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    const stale = component['exportData']();
    await flushUntil(() => downloadHouseholdExport.mock.calls.length === 1);
    setOwnerSession('export-b');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length >= 2);
    const current = component['exportData']();
    await flushUntil(() => downloadHouseholdExport.mock.calls.length === 2);

    oldRequest.resolve({
      blob: new Blob(['old-session-bytes'], { type: 'application/zip' }),
      filename: 'old-session.zip',
    });
    await stale;
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
    expect(component['exporting']()).toBe(true);
    expect(component['exportError']()).toBeNull();

    currentRequest.resolve({
      blob: new Blob(['current-session-bytes'], { type: 'application/zip' }),
      filename: 'current-session.zip',
    });
    await current;
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(component['exporting']()).toBe(false);
    clickSpy.mockRestore();
    fixture.destroy();
    clearAuthState();
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
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValue(
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

  it('maps and validates independent destination drafts and activates explicitly', async () => {
    const updated = backupConfig({
      revision: 'revision-2',
      updated_at: '2026-09-10T12:01:00Z',
      retention_review_required: false,
      retention_activated_at: '2026-09-10T12:01:00Z',
    });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi.fn().mockResolvedValue(response(updated)),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    expect(component['localDraft']().maxGB).toBe(10);
    expect(component['offboxDraft']().maxGB).toBe(50);
    component['updateDraft']('local', { keepAllDays: 31, dailyUntilDays: 14 });
    expect(component['retentionValidation']()).toContain('no greater than');
    expect(apiMock.updateBackupConfig).not.toHaveBeenCalled();

    component['updateDraft']('local', {
      keepAllDays: 2,
      dailyUntilDays: 21,
      weeklyUntilDays: 120,
      maxGB: 12,
      reserveGB: 1.5,
    });
    component['updateDraft']('offbox', { mode: 'keep_all', maxGB: 80, reserveGB: 4 });
    await component['saveAndActivateRetention']();

    expect(apiMock.updateBackupConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 'revision-1',
        confirm_retention_policy: true,
        local_retention: {
          mode: 'tiered',
          keep_all_days: 2,
          daily_until_days: 21,
          weekly_until_days: 120,
        },
        offbox_retention: {
          mode: 'keep_all',
          keep_all_days: null,
          daily_until_days: null,
          weekly_until_days: null,
        },
        local_max_bytes: 12_000_000_000,
        offbox_max_bytes: 80_000_000_000,
        local_min_free_bytes: 1_500_000_000,
        offbox_min_free_bytes: 4_000_000_000,
      }),
    );
  });

  it('shows the pending preview and preserves a draft across a 409 until reconciliation', async () => {
    const current = backupConfig({
      revision: 'revision-current',
      updated_at: '2026-09-10T12:02:00Z',
      local_pending_prune_count: 7,
    });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockResolvedValueOnce(response(current)),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValue(response(undefined, { error: { message: 'conflict' } }, 409)),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance['updateDraft']('local', { weeklyUntilDays: 365 });
    await fixture.componentInstance['saveAndActivateRetention']();
    fixture.detectChanges();

    expect(fixture.componentInstance['localDraft']().weeklyUntilDays).toBe(365);
    expect(fixture.componentInstance['blockedSave']()?.current?.revision).toBe('revision-current');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Current saved-policy preview: 2 backups');
    expect(text).toContain('Backup settings need reconciliation');
    expect(text).toContain('Use current box settings');
    expect(text).toContain('Retry preserved settings');

    fixture.componentInstance['useCurrentBlockedConfig']();
    expect(fixture.componentInstance['localDraft']().weeklyUntilDays).toBe(90);
  });

  it('serializes automatic saves and coalesces repeated blur edits', async () => {
    const first = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => first.promise)
        .mockResolvedValueOnce(
          response(
            backupConfig({
              revision: 'revision-3',
              updated_at: '2026-09-10T12:02:00Z',
              smb_host: 'newest.local',
            }),
          ),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance['host'].set('first.local');
    const firstSave = fixture.componentInstance['saveConfig']();
    await Promise.resolve();
    fixture.componentInstance['host'].set('newest.local');
    const secondSave = fixture.componentInstance['saveConfig']();
    fixture.componentInstance['host'].set('newest.local');
    void fixture.componentInstance['saveConfig']();
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);

    first.resolve(
      response(
        backupConfig({
          revision: 'revision-2',
          updated_at: '2026-09-10T12:01:00Z',
          smb_host: 'first.local',
        }),
      ),
    );
    await Promise.all([firstSave, secondSave]);
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0].smb_host).toBe('newest.local');
    expect(apiMock.updateBackupConfig.mock.calls[1][0].expected_revision).toBe('revision-2');
    expect(apiMock.updateBackupConfig.mock.calls[1][0]).not.toHaveProperty('expected_updated_at');
  });

  it('supersedes one unsent 90-day activation with the visible 365-day policy', async () => {
    const automatic = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => automatic.promise)
        .mockResolvedValueOnce(
          response(
            backupConfig({
              revision: 'revision-3',
              retention_review_required: false,
              local_retention: {
                ...backupConfig().local_retention,
                weekly_until_days: 365,
              },
            }),
          ),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('queued.local');
    const automaticSave = component['saveConfig']('destination');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    void component['saveAndActivateRetention']();
    const older = component['pendingActivation'];
    expect(older?.confirmedRetention?.local_retention?.weekly_until_days).toBe(90);

    component['updateDraft']('local', { weeklyUntilDays: 365 });
    fixture.detectChanges();
    let activationButton = [
      ...(fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ].find((button) =>
      button.textContent?.includes('Save and activate current settings'),
    ) as HTMLButtonElement;
    expect(activationButton.disabled).toBe(false);
    activationButton.click();

    const replacement = component['pendingActivation'];
    expect(replacement?.id).not.toBe(older?.id);
    expect(replacement?.owner).toEqual(older?.owner);
    expect(replacement?.confirmedRetention?.local_retention?.weekly_until_days).toBe(365);
    await component['saveAndActivateRetention']();
    expect(component['pendingActivation']?.id).toBe(replacement?.id);
    fixture.detectChanges();
    activationButton = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (button) => button.textContent?.includes('Activation pending'),
    ) as HTMLButtonElement;
    expect(activationButton.disabled).toBe(true);

    automatic.resolve(response(backupConfig({ revision: 'revision-2', smb_host: 'queued.local' })));
    await automaticSave;

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        expected_revision: 'revision-2',
        confirm_retention_policy: true,
        smb_host: 'queued.local',
      }),
    );
    expect(apiMock.updateBackupConfig.mock.calls[1][0].local_retention.weekly_until_days).toBe(365);
  });

  it('disables stale Retry during reconciliation and replaces its queued activation once', async () => {
    const automatic = deferred<ReturnType<typeof response>>();
    const reconciliation = deferred<ReturnType<typeof response>>();
    const replacementResponse = deferred<ReturnType<typeof response>>();
    const current = backupConfig({ revision: 'revision-current', smb_host: 'current.local' });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockImplementationOnce(() => reconciliation.promise),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => automatic.promise)
        .mockImplementationOnce(() => replacementResponse.promise),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('draft.local');
    const automaticSave = component['saveConfig']('destination');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    void component['saveAndActivateRetention']();
    const older = component['pendingActivation'];
    expect(older?.confirmedRetention?.local_retention?.weekly_until_days).toBe(90);
    component['updateDraft']('local', { weeklyUntilDays: 365 });

    automatic.resolve(response(undefined, { error: { message: 'busy' } }, 409));
    await flushUntil(() => component['blockedSave']()?.phase === 'refreshing');
    fixture.detectChanges();
    let activationButton = [
      ...(fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ].find((button) =>
      button.textContent?.includes('Save and activate current settings'),
    ) as HTMLButtonElement;
    expect(component['canRetryBlockedSave']()).toBe(false);
    expect(activationButton.disabled).toBe(true);

    reconciliation.resolve(response(current));
    await automaticSave;
    expect(component['blockedSave']()?.phase).toBe('ready');
    expect(component['canRetryBlockedSave']()).toBe(false);
    fixture.detectChanges();
    const retryButton = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (button) => button.textContent?.includes('Retry preserved settings'),
    ) as HTMLButtonElement;
    activationButton = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (button) => button.textContent?.includes('Save and activate current settings'),
    ) as HTMLButtonElement;
    expect(retryButton.disabled).toBe(true);
    expect(activationButton.disabled).toBe(false);

    await component['retryBlockedDraft']();
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);
    expect(component['pendingActivation']?.id).toBe(older?.id);
    activationButton.click();
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 2);

    const replacement = component['inFlightSave']();
    expect(component['pendingActivation']).toBeNull();
    expect(replacement?.kind).toBe('activation');
    expect(replacement?.id).not.toBe(older?.id);
    expect(replacement?.owner).toEqual(older?.owner);
    expect(replacement?.confirmedRetention?.local_retention?.weekly_until_days).toBe(365);
    expect(apiMock.updateBackupConfig.mock.calls[1][0].expected_revision).toBe('revision-current');
    expect(apiMock.updateBackupConfig.mock.calls[1][0].local_retention.weekly_until_days).toBe(365);
    fixture.detectChanges();
    activationButton = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (button) => button.textContent?.includes('Activation pending'),
    ) as HTMLButtonElement;
    expect(activationButton.disabled).toBe(true);
    activationButton.click();
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);

    replacementResponse.resolve(
      response(
        backupConfig({
          revision: 'revision-activated',
          smb_host: 'draft.local',
          retention_review_required: false,
          local_retention: {
            ...backupConfig().local_retention,
            weekly_until_days: 365,
          },
        }),
      ),
    );
    await fixture.whenStable();
    expect(component['blockedSave']()).toBeNull();
    expect(component['serverConfig']()?.revision).toBe('revision-activated');
  });

  it('keeps one queued activation through autosave and reconciliation failure until explicit retry', async () => {
    const automatic = deferred<ReturnType<typeof response>>();
    const current = backupConfig({ revision: 'revision-current', smb_host: 'other.local' });
    const activated = backupConfig({
      revision: 'revision-activated',
      smb_host: 'latest.local',
      retention_review_required: false,
      retention_activated_at: '2026-09-10T12:05:00Z',
    });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockResolvedValueOnce(response(undefined, { error: { message: 'reload offline' } }))
        .mockResolvedValueOnce(response(current)),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => automatic.promise)
        .mockResolvedValueOnce(response(activated)),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('first.local');
    const automaticSave = component['saveConfig']('destination');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    fixture.detectChanges();
    const activationButton = [
      ...(fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ].find((button) =>
      button.textContent?.includes('Save and activate retention'),
    ) as HTMLButtonElement;
    expect(activationButton.disabled).toBe(false);
    activationButton.click();
    void component['saveAndActivateRetention']();
    component['host'].set('latest.local');
    component['onDestinationEdit']();
    void component['saveConfig']('destination');
    fixture.detectChanges();

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);
    expect(component['activationPending']()).toBe(true);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Activation pending');
    automatic.resolve(response(undefined, { error: { message: 'busy' } }, 409));
    await automaticSave;

    expect(component['blockedSave']()?.phase).toBe('refreshFailed');
    expect(component['canRetryBlockedSave']()).toBe(false);
    expect(component['pendingActivation']).not.toBeNull();
    expect(component['destinationSaveFeedback']()?.level).toBe('error');
    expect(component['retentionSaveFeedback']()?.level).toBe('error');
    expect(component['serverConfig']()?.retention_review_required).toBe(true);
    fixture.detectChanges();
    expect(
      (fixture.nativeElement as HTMLElement).querySelectorAll('.config-feedback[role="alert"]')
        .length,
    ).toBeGreaterThanOrEqual(2);
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);

    await component['reloadBlockedSave']();
    expect(component['blockedSave']()?.phase).toBe('ready');
    const retry = component['retryBlockedDraft']();
    const duplicateRetry = component['retryBlockedDraft']();
    await Promise.all([retry, duplicateRetry]);

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    const retryPayload = apiMock.updateBackupConfig.mock.calls[1][0];
    expect(retryPayload.expected_revision).toBe('revision-current');
    expect(retryPayload.smb_host).toBe('latest.local');
    expect(retryPayload.confirm_retention_policy).toBe(true);
    expect(retryPayload).not.toHaveProperty('expected_updated_at');
    expect(component['blockedSave']()).toBeNull();
    expect(component['retentionSaveFeedback']()?.level).toBe('success');
    expect(component['serverConfig']()?.retention_review_required).toBe(false);
  });

  it('sends queued activation with the successful autosave response revision', async () => {
    const automatic = deferred<ReturnType<typeof response>>();
    const postSaveStatus = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi
        .fn()
        .mockResolvedValueOnce(response(recoveryStatus()))
        .mockImplementationOnce(() => postSaveStatus.promise),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => automatic.promise)
        .mockResolvedValueOnce(
          response(
            backupConfig({
              revision: 'revision-3',
              frequency: 'weekly',
              retention_review_required: false,
            }),
          ),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['frequency'].set('weekly');
    const save = component['saveConfig']('schedule');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    void component['saveAndActivateRetention']();
    automatic.resolve(response(backupConfig({ revision: 'revision-2', frequency: 'weekly' })));
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 2);
    // A best-effort recovery refresh cannot hold queued activation hostage.
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(1);
    await flushUntil(() => apiMock.getBackupRecoveryStatus.mock.calls.length === 2);
    postSaveStatus.resolve(response(recoveryStatus()));
    await save;

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        expected_revision: 'revision-2',
        frequency: 'weekly',
        confirm_retention_policy: true,
      }),
    );
  });

  it('keeps in-flight activation immutable and saves later operational edits separately', async () => {
    const activationResponse = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => activationResponse.promise)
        .mockResolvedValueOnce(
          response(
            backupConfig({
              revision: 'revision-3',
              smb_host: 'newer.local',
              retention_review_required: false,
            }),
          ),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['updateDraft']('local', { weeklyUntilDays: 120 });
    const activation = component['saveAndActivateRetention']();
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    const firstPayload = apiMock.updateBackupConfig.mock.calls[0][0];
    component['updateDraft']('local', { weeklyUntilDays: 365 });
    component['host'].set('newer.local');
    component['onDestinationEdit']();
    const laterSave = component['saveConfig']('destination');

    expect(firstPayload.local_retention.weekly_until_days).toBe(120);
    expect(firstPayload.smb_host).toBe('nas.local');
    activationResponse.resolve(
      response(
        backupConfig({
          revision: 'revision-2',
          retention_review_required: false,
          local_retention: {
            ...backupConfig().local_retention,
            weekly_until_days: 120,
          },
        }),
      ),
    );
    await Promise.all([activation, laterSave]);

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        expected_revision: 'revision-2',
        smb_host: 'newer.local',
      }),
    );
    expect(apiMock.updateBackupConfig.mock.calls[1][0].confirm_retention_policy).toBeUndefined();
    expect(component['localDraft']().weeklyUntilDays).toBe(365);
    expect(component['retentionSaveFeedback']()?.level).not.toBe('success');
  });

  it('clears only the acknowledged password generation', async () => {
    const first = deferred<ReturnType<typeof response>>();
    const second = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockImplementationOnce(() => first.promise)
        .mockImplementationOnce(() => second.promise),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['password'].set('first-password');
    component['onPasswordInput']();
    const firstSave = component['saveConfig']('destination');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    component['password'].set('newer-password');
    component['onPasswordInput']();
    const newerSave = component['saveConfig']('destination');

    first.resolve(response(backupConfig({ revision: 'revision-2' })));
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 2);
    expect(component['password']()).toBe('newer-password');
    expect(apiMock.updateBackupConfig.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        expected_revision: 'revision-2',
        smb_password: 'newer-password',
      }),
    );
    second.resolve(response(backupConfig({ revision: 'revision-3' })));
    await Promise.all([firstSave, newerSave]);
    expect(component['password']()).toBe('');
    expect(component['passwordEdited']).toBe(false);
  });

  it('submits a corrected new operational intent after a 422 without replaying automatically', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(undefined, { error: { message: 'invalid host' } }, 422))
        .mockResolvedValueOnce(
          response(backupConfig({ revision: 'revision-corrected', smb_host: 'corrected.local' })),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('invalid host');
    await component['saveConfig']('destination');
    expect(component['blockedSave']()?.failureClass).toBe('validation');
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);

    component['host'].set('corrected.local');
    component['onDestinationEdit']();
    await component['saveConfig']('destination');
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0].smb_host).toBe('corrected.local');
    expect(component['blockedSave']()).toBeNull();
  });

  it('requires corrected reconfirmation after 422 and never falls back from 428', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockResolvedValueOnce(response(backupConfig({ revision: 'revision-428-current' }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(undefined, { error: { message: 'invalid policy' } }, 422))
        .mockResolvedValueOnce(response(backupConfig({ revision: 'revision-2' })))
        .mockResolvedValueOnce(
          response(undefined, { error: { message: 'revision required' } }, 428),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['updateDraft']('local', { weeklyUntilDays: 120 });
    await component['saveAndActivateRetention']();
    expect(component['blockedSave']()?.failureClass).toBe('validation');
    expect(component['canRetryBlockedSave']()).toBe(false);

    component['updateDraft']('local', { weeklyUntilDays: 121 });
    await component['saveAndActivateRetention']();
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0].local_retention.weekly_until_days).toBe(121);

    component['host'].set('precondition.local');
    await component['saveConfig']('destination');
    expect(component['blockedSave']()?.failureClass).toBe('precondition');
    expect(component['blockedSave']()?.phase).toBe('ready');
    expect(component['canRetryBlockedSave']()).toBe(false);
    for (const [payload] of apiMock.updateBackupConfig.mock.calls) {
      expect(payload.expected_revision).toBeTruthy();
      expect(payload).not.toHaveProperty('expected_updated_at');
    }
  });

  it('requires authentication recovery and an explicit retry after 401 or 403', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockResolvedValueOnce(response(backupConfig({ revision: 'revision-auth-current' }))),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(undefined, { error: { message: 'sign in' } }, 401))
        .mockResolvedValueOnce(response(backupConfig({ revision: 'revision-auth-saved' }))),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('auth.local');
    await component['saveConfig']('destination');
    expect(component['blockedSave']()?.phase).toBe('refreshFailed');
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);
    await component['reloadBlockedSave']();
    expect(component['canRetryBlockedSave']()).toBe(true);
    await component['retryBlockedDraft']();
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(2);
    expect(apiMock.updateBackupConfig.mock.calls[1][0].expected_revision).toBe(
      'revision-auth-current',
    );
    expect(component['classifySaveFailure'](403)).toBe('authentication');
  });

  it('acknowledges an exact non-secret match after an ambiguous response without retrying', async () => {
    const matching = backupConfig({ revision: 'revision-matched', smb_host: 'matched.local' });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockResolvedValueOnce(response(matching)),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValue(response(undefined, { error: { message: 'connection reset' } })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('matched.local');
    await component['saveConfig']('destination');

    expect(component['blockedSave']()).toBeNull();
    expect(component['serverConfig']()?.revision).toBe('revision-matched');
    expect(component['destinationSaveFeedback']()?.level).toBe('info');
    expect(component['destinationSaveFeedback']()?.message).toContain('response was not confirmed');
    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);
  });

  it('discards queued activation and late save effects across session A→B→A', async () => {
    setOwnerSession('save-a');
    const oldSave = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig({ revision: 'revision-a' })))
        .mockResolvedValueOnce(
          response(backupConfig({ revision: 'revision-b', smb_host: null, has_password: false })),
        )
        .mockResolvedValueOnce(
          response(backupConfig({ revision: 'revision-a2', smb_host: null, has_password: false })),
        ),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi.fn().mockImplementation(() => oldSave.promise),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('old.local');
    const automatic = component['saveConfig']('destination');
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    const activation = component['saveAndActivateRetention']();
    expect(component['pendingActivation']).not.toBeNull();

    setOwnerSession('save-b');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length === 2);
    setOwnerSession('save-a');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length === 3);
    oldSave.resolve(response(backupConfig({ revision: 'revision-old-completion' })));
    await Promise.all([automatic, activation]);

    expect(apiMock.updateBackupConfig).toHaveBeenCalledTimes(1);
    expect(component['serverConfig']()?.revision).toBe('revision-a2');
    expect(component['pendingActivation']).toBeNull();
    expect(component['blockedSave']()).toBeNull();
    expect(component['retentionSaveFeedback']()).toBeNull();
    fixture.destroy();
    clearAuthState();
  });

  it('renders review-only recovery as an actionable presentation without masking mixed faults', async () => {
    const reviewOnly = destination('local', {
      status: 'degraded',
      coverage_status: 'unknown',
      retention_review_required: true,
      reason_codes: ['retention_review_required', 'coverage_unknown'],
    });
    const reviewOnlyKeepAll = destination('offbox', {
      status: 'degraded',
      coverage_status: 'not_applicable',
      retention_review_required: true,
      reason_codes: ['retention_review_required'],
      policy: {
        mode: 'keep_all',
        keep_all_days: null,
        daily_until_days: null,
        weekly_until_days: null,
        target_oldest_at: null,
      },
    });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(
        response(
          recoveryStatus({
            overall_status: 'degraded',
            local: reviewOnly,
            offbox: reviewOnlyKeepAll,
          }),
        ),
      ),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Review required overall');
    expect(text).toContain(
      'Automatic pruning is paused until an administrator activates retention',
    );
    expect(text).toContain('Coverage does not apply to this destination.');
    expect(host.querySelector('.recovery-destination')?.getAttribute('aria-label')).toContain(
      'API recovery status is degraded',
    );
    expect(fixture.componentInstance['isReviewOnlyDestination'](reviewOnly as never)).toBe(true);

    const mixed = destination('local', {
      status: 'degraded',
      retention_review_required: true,
      reason_codes: ['retention_review_required', 'coverage_unknown', 'read_probe_partial'],
    });
    expect(fixture.componentInstance['isReviewOnlyDestination'](mixed as never)).toBe(false);
    expect(fixture.componentInstance['recoveryStateLabel'](mixed as never)).toBe('Degraded');
    expect(
      fixture.componentInstance['isOverallReviewOnly'](
        recoveryStatus({ overall_status: 'degraded', local: reviewOnly, offbox: mixed }) as never,
      ),
    ).toBe(false);
    expect(fixture.componentInstance['isOverallReviewOnly'](recoveryStatus() as never)).toBe(false);

    fixture.componentInstance['recoveryStatus'].set(
      recoveryStatus({
        overall_status: 'degraded',
        local: mixed,
        offbox: reviewOnlyKeepAll,
      }) as never,
    );
    fixture.detectChanges();
    const localCard = host.querySelectorAll('.recovery-destination')[0];
    expect(localCard.textContent).toContain('Degraded');
    expect(localCard.textContent).toContain('Retention still requires administrator review');
  });

  it('renders schedule and destination success beside their initiating controls', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(
          response(backupConfig({ revision: 'revision-2', frequency: 'weekly' })),
        )
        .mockResolvedValueOnce(
          response(
            backupConfig({ revision: 'revision-3', frequency: 'weekly', smb_host: 'saved.local' }),
          ),
        ),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['onScheduleChange']('weekly');
    await flushUntil(() => component['scheduleSaveFeedback']()?.level === 'success');
    component['host'].set('saved.local');
    component['onDestinationEdit']();
    await component['saveConfig']('destination');
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.config-control-feedback [role="status"]')?.textContent).toContain(
      'Schedule saved',
    );
    const destinationFeedback = [...host.querySelectorAll('.config-feedback[role="status"]')].find(
      (element) => element.textContent?.includes('Destination saved'),
    );
    expect(destinationFeedback).toBeTruthy();
    expect(destinationFeedback?.closest('.backups-card')?.textContent).toContain(
      'Off-box backup — Synology',
    );
  });

  it('renders truthful constrained, unavailable, probe, coverage, capacity, and timestamp states accessibly', async () => {
    const observed = recoveryStatus({
      overall_status: 'unavailable',
      local: destination('local', {
        status: 'constrained',
        coverage_status: 'shortened',
        probe_status: 'partial',
        readable_archive_count: null,
        protected_anomaly_count: 2,
        capacity: capacity('unknown'),
      }),
      offbox: destination('offbox', {
        status: 'unavailable',
        coverage_status: 'unknown',
        probe_status: 'unavailable',
        oldest_readable_at: '2026-04-01T00:00:00Z',
        oldest_timestamp_source: 'remote_modified_at',
        capacity: capacity('unavailable'),
      }),
    });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(observed)),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Storage limits shortened the configured recovery window.');
    expect(text).toContain('Capacity could not be measured; backups will still be attempted.');
    expect(text).toContain('Read probe partial');
    expect(text).toContain('Not known');
    expect(text).toContain('Synology inventory is unavailable, so its recovery window is unknown.');
    expect(text).toContain("uses the file's modified time");
    expect(text).toContain('Archive integrity and key correctness are checked during restore.');
    expect(host.querySelectorAll('.recovery-destination[role="alert"]')).toHaveLength(2);
    expect(host.querySelectorAll('.retention-destination')).toHaveLength(2);
    expect(text).not.toContain('last 7');
    expect(text).not.toContain('When all backups combined exceed the limit');
  });

  it('lets only the newest status completion win and clears stale status on a current failure', async () => {
    const oldRequest = deferred<ReturnType<typeof response>>();
    const fresh = recoveryStatus({ as_of: '2026-09-10T13:00:00Z' });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi
        .fn()
        .mockResolvedValueOnce(response(recoveryStatus()))
        .mockImplementationOnce(() => oldRequest.promise)
        .mockResolvedValueOnce(response(fresh))
        .mockResolvedValueOnce(response(undefined, { error: { message: 'offline' } })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const older = fixture.componentInstance['refreshRecoveryStatus']();
    const newer = fixture.componentInstance['refreshRecoveryStatus']();
    await newer;
    oldRequest.resolve(response(undefined, { error: { message: 'old failure' } }));
    await older;
    expect(fixture.componentInstance['recoveryStatus']()?.as_of).toBe('2026-09-10T13:00:00Z');
    expect(fixture.componentInstance['recoveryStatusError']()).toBeNull();

    await fixture.componentInstance['refreshRecoveryStatus']();
    expect(fixture.componentInstance['recoveryStatus']()).toBeNull();
    expect(fixture.componentInstance['recoveryStatusError']()).toContain('offline');
  });

  it('rejects a late recovery completion from a previous authenticated session', async () => {
    setAuthState({
      accessToken: 'token-a',
      householdId: 'household-a',
      userId: 'user-a',
      role: 'owner',
      rights: ['backups.manage'],
    });
    const oldSession = deferred<ReturnType<typeof response>>();
    const newSessionStatus = recoveryStatus({ as_of: '2026-09-10T14:00:00Z' });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi
        .fn()
        .mockImplementationOnce(() => oldSession.promise)
        .mockResolvedValueOnce(response(newSessionStatus)),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    for (let i = 0; i < 5 && apiMock.getBackupRecoveryStatus.mock.calls.length === 0; i++) {
      await Promise.resolve();
    }

    setAuthState({
      accessToken: 'token-b',
      householdId: 'household-b',
      userId: 'user-b',
      role: 'owner',
      rights: ['backups.manage'],
    });
    for (let i = 0; i < 10 && apiMock.getBackupRecoveryStatus.mock.calls.length < 2; i++) {
      await Promise.resolve();
    }
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
    for (let i = 0; i < 5 && !fixture.componentInstance['recoveryStatus'](); i++) {
      await Promise.resolve();
    }
    oldSession.resolve(response(recoveryStatus({ as_of: '2026-09-10T11:00:00Z' })));
    await Promise.resolve();
    await Promise.resolve();

    expect(fixture.componentInstance['recoveryStatus']()?.as_of).toBe('2026-09-10T14:00:00Z');
    fixture.destroy();
    clearAuthState();
  });

  it('surfaces a successful remote-list response whose inventory is unavailable', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(
        response({
          backups: [],
          status: 'unavailable',
          as_of: '2026-09-10T14:00:00Z',
          reason: 'The Synology inventory is unavailable.',
        }),
      ),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.componentInstance['remoteBackups']()).toEqual([]);
    expect(fixture.componentInstance['remoteListError']()).toContain('unavailable');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'The Synology inventory is unavailable.',
    );
  });

  it('rejects a revealed backup key from a replaced authenticated session', async () => {
    setAuthState({
      accessToken: 'key-token-a',
      householdId: 'key-household-a',
      userId: 'key-user-a',
      role: 'owner',
      rights: ['backups.manage'],
    });
    const oldKey = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [], status: 'available' })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      getBackupEncryptionKey: vi.fn().mockImplementation(() => oldKey.promise),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const reveal = fixture.componentInstance['revealKey']();
    await flushUntil(() => apiMock.getBackupEncryptionKey.mock.calls.length === 1);
    setAuthState({
      accessToken: 'key-token-b',
      householdId: 'key-household-b',
      userId: 'key-user-b',
      role: 'owner',
      rights: ['backups.manage'],
    });
    oldKey.resolve(response({ configured: true, key: 'old-session-secret' }));
    await reveal;

    expect(fixture.componentInstance['revealedKey']()).toBeNull();
    fixture.destroy();
    clearAuthState();
  });

  it('owns key status refresh and key revelation independently', async () => {
    const refreshedStatus = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [], status: 'available' })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi
        .fn()
        .mockResolvedValueOnce(response(keyStatus()))
        .mockImplementationOnce(() => refreshedStatus.promise),
      getBackupEncryptionKey: vi
        .fn()
        .mockResolvedValue(response({ configured: true, key: 'current-session-secret' })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const statusRefresh = fixture.componentInstance['loadKeyStatus']();
    await flushUntil(() => apiMock.getHouseholdKeyStatus.mock.calls.length === 2);
    await fixture.componentInstance['revealKey']();
    refreshedStatus.resolve(response(keyStatus({ device_wraps: 9 })));
    await statusRefresh;

    expect(fixture.componentInstance['revealedKey']()).toBe('current-session-secret');
    expect(fixture.componentInstance['keyStatus']()?.device_wraps).toBe(9);
  });

  it.each(backupMutations)(
    'clears contradictory feedback and refreshes recovery after a lost %s response',
    async (mutation) => {
      const mutationResponse = deferred<ReturnType<typeof response>>();
      const committed = recoveryStatus({
        as_of: `2026-09-10T15:00:0${backupMutations.indexOf(mutation)}Z`,
      });
      const mutate = vi.fn().mockImplementation(() => mutationResponse.promise);
      const apiMock = {
        listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
        listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
        getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
        getBackupRecoveryStatus: vi
          .fn()
          .mockResolvedValueOnce(response(recoveryStatus()))
          .mockResolvedValueOnce(response(committed)),
        getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
        createBackup: mutate,
        restoreBackup: mutate,
        restoreRemoteBackup: mutate,
        deleteBackup: mutate,
        deleteRemoteBackup: mutate,
      };
      configure(apiMock, 'owner');
      const fixture = TestBed.createComponent(Backups);
      fixture.detectChanges();
      await fixture.whenStable();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      const component = fixture.componentInstance;
      component['actionError'].set('stale failure');
      component['statusMessage'].set('stale success');

      const action = invokeMutation(component, mutation);
      expect(component['actionError']()).toBeNull();
      expect(component['statusMessage']()).toBeNull();
      expect(component['busy']()).toBe(true);

      mutationResponse.resolve(
        response(undefined, { error: { message: 'response lost after commit' } }),
      );
      await action;

      expect(component['busy']()).toBe(false);
      expect(component['actionError']()).toContain('response lost after commit');
      expect(component['statusMessage']()).toBeNull();
      expect(component['recoveryStatus']()?.as_of).toBe(committed.as_of);
      expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
      if (mutation === 'create' || mutation === 'restore-remote' || mutation === 'delete-remote') {
        expect(apiMock.listRemoteBackups).toHaveBeenCalledTimes(2);
      }
      if (mutation === 'restore-local' || mutation === 'restore-remote') {
        expect(apiMock.getBackupConfig).toHaveBeenCalledTimes(2);
      }
      confirmSpy.mockRestore();
    },
  );

  it.each(backupMutations)(
    'lets only the newest %s mutation refresh publish recovery state',
    async (mutation) => {
      const olderRefresh = deferred<ReturnType<typeof response>>();
      const fresh = recoveryStatus({
        as_of: `2026-09-10T16:00:0${backupMutations.indexOf(mutation)}Z`,
      });
      const mutate = vi.fn().mockResolvedValue(response({ id: 'newest', status: 'completed' }));
      const apiMock = {
        listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
        listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
        getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
        getBackupRecoveryStatus: vi
          .fn()
          .mockResolvedValueOnce(response(recoveryStatus()))
          .mockImplementationOnce(() => olderRefresh.promise)
          .mockResolvedValueOnce(response(fresh)),
        getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
        createBackup: mutate,
        restoreBackup: mutate,
        restoreRemoteBackup: mutate,
        deleteBackup: mutate,
        deleteRemoteBackup: mutate,
      };
      configure(apiMock, 'owner');
      const fixture = TestBed.createComponent(Backups);
      fixture.detectChanges();
      await fixture.whenStable();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      const component = fixture.componentInstance;

      const older = invokeMutation(component, mutation);
      await flushUntil(() => apiMock.getBackupRecoveryStatus.mock.calls.length === 2);
      const newer = invokeMutation(component, mutation);
      await newer;
      olderRefresh.resolve(response(recoveryStatus({ as_of: '2026-09-10T10:00:00Z' })));
      await older;

      expect(component['recoveryStatus']()?.as_of).toBe(fresh.as_of);
      expect(component['recoveryStatusError']()).toBeNull();
      expect(component['actionError']()).toBeNull();
      expect(component['statusMessage']()).not.toBeNull();
      expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(3);
      confirmSpy.mockRestore();
    },
  );

  it('carries a superseded restore config refresh into a newer create', async () => {
    const oldConfig = deferred<ReturnType<typeof response>>();
    const freshConfig = backupConfig({ updated_at: '2026-09-10T17:00:00Z' });
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi
        .fn()
        .mockResolvedValueOnce(response(backupConfig()))
        .mockImplementationOnce(() => oldConfig.promise)
        .mockResolvedValueOnce(response(freshConfig)),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      restoreBackup: vi.fn().mockResolvedValue(response({ status: 'completed' })),
      createBackup: vi.fn().mockResolvedValue(response({ status: 'completed' })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const component = fixture.componentInstance;

    const restore = component['restore']('local-1');
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length === 2);
    await component['createBackup']();
    oldConfig.resolve(response(backupConfig({ updated_at: '2026-09-10T16:00:00Z' })));
    await restore;

    expect(apiMock.getBackupConfig).toHaveBeenCalledTimes(3);
    expect(component['serverConfig']()?.updated_at).toBe(freshConfig.updated_at);
    expect(component['statusMessage']()).toBe('Backup complete.');
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
    confirmSpy.mockRestore();
  });

  it('carries a superseded remote-delete refresh into a newer local delete', async () => {
    const oldRemote = deferred<ReturnType<typeof response>>();
    const freshRemote = [{ filename: 'fresh.tar', modified_at: 2, size_bytes: 20 }];
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi
        .fn()
        .mockResolvedValueOnce(response({ backups: [] }))
        .mockImplementationOnce(() => oldRemote.promise)
        .mockResolvedValueOnce(response({ backups: freshRemote })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      deleteRemoteBackup: vi.fn().mockResolvedValue(response(undefined)),
      deleteBackup: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const component = fixture.componentInstance;

    const remoteDelete = component['deleteRemote']('old.tar');
    await flushUntil(() => apiMock.listRemoteBackups.mock.calls.length === 2);
    await component['deleteLocal']('local-1');
    oldRemote.resolve(
      response({ backups: [{ filename: 'stale.tar', modified_at: 1, size_bytes: 10 }] }),
    );
    await remoteDelete;

    expect(apiMock.listRemoteBackups).toHaveBeenCalledTimes(3);
    expect(component['remoteBackups']().map((backup) => backup.filename)).toEqual(['fresh.tar']);
    expect(component['statusMessage']()).toBe('On-box backup deleted.');
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
    confirmSpy.mockRestore();
  });

  it('does not let a save completion replace newer mutation feedback or status', async () => {
    const saveResponse = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      updateBackupConfig: vi.fn().mockImplementation(() => saveResponse.promise),
      createBackup: vi.fn().mockResolvedValue(response({ status: 'completed' })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['host'].set('changed.local');
    const save = component['saveConfig']();
    await flushUntil(() => apiMock.updateBackupConfig.mock.calls.length === 1);
    await component['createBackup']();
    saveResponse.resolve(
      response(backupConfig({ updated_at: '2026-09-10T17:15:00Z', smb_host: 'changed.local' })),
    );
    await save;

    expect(component['statusMessage']()).toBe('Backup complete.');
    expect(component['actionError']()).toBeNull();
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
  });

  it('does not let a destination probe replace newer mutation feedback or status', async () => {
    const probeResponse = deferred<ReturnType<typeof response>>();
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      checkBackupDestination: vi.fn().mockImplementation(() => probeResponse.promise),
      createBackup: vi.fn().mockResolvedValue(response({ status: 'completed' })),
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    const probe = component['testConnection']();
    await flushUntil(() => apiMock.checkBackupDestination.mock.calls.length === 1);
    await component['createBackup']();
    probeResponse.resolve(response(undefined, { error: { message: 'late probe failure' } }));
    await probe;

    expect(component['statusMessage']()).toBe('Backup complete.');
    expect(component['actionError']()).toBeNull();
    expect(apiMock.getBackupRecoveryStatus).toHaveBeenCalledTimes(2);
  });

  it('rejects a reverse-order create completion from a replaced authenticated session', async () => {
    setAuthState({
      accessToken: 'create-token-a',
      householdId: 'create-household-a',
      userId: 'create-user-a',
      role: 'owner',
      rights: ['backups.manage'],
    });
    const previousSession = deferred<ReturnType<typeof response>>();
    const createBackup = vi
      .fn()
      .mockImplementationOnce(() => previousSession.promise)
      .mockResolvedValueOnce(
        response({ status: 'completed', completed_at: '2026-09-10T16:30:00Z' }),
      );
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response(backupConfig())),
      getBackupRecoveryStatus: vi.fn().mockResolvedValue(response(recoveryStatus())),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(keyStatus())),
      createBackup,
    };
    configure(apiMock, 'owner');
    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    const staleAction = component['createBackup']();
    await flushUntil(() => createBackup.mock.calls.length === 1);
    setAuthState({
      accessToken: 'create-token-b',
      householdId: 'create-household-a',
      userId: 'create-user-a',
      role: 'owner',
      rights: ['backups.manage'],
    });
    await flushUntil(() => apiMock.getBackupConfig.mock.calls.length >= 2);
    await component['createBackup']();
    previousSession.resolve(
      response({ status: 'completed', completed_at: '2026-09-10T09:30:00Z' }),
    );
    await staleAction;

    expect(component['latest']()?.completed_at).toBe('2026-09-10T16:30:00Z');
    expect(component['statusMessage']()).toBe('Backup complete.');
    expect(component['actionError']()).toBeNull();
    expect(createBackup).toHaveBeenCalledTimes(2);
    fixture.destroy();
    clearAuthState();
  });

  it('maps every contract state to human wording', () => {
    const apiMock = { listBackups: vi.fn().mockResolvedValue(response({ backups: [] })) };
    configure(apiMock, 'owner');
    const component = TestBed.createComponent(Backups).componentInstance;
    for (const state of [
      'not_configured',
      'empty',
      'healthy',
      'constrained',
      'degraded',
      'unavailable',
    ] as const) {
      expect(component['destinationStateLabel'](state)).not.toBe(state);
    }
    for (const state of [
      'not_applicable',
      'empty',
      'building',
      'met',
      'incomplete',
      'shortened',
      'unknown',
    ] as const) {
      expect(component['coverageLabel'](state)).not.toBe(state);
      expect(component['coverageDescription'](state)).toMatch(/\.$/);
    }
    for (const state of ['ok', 'warning', 'insufficient', 'unknown', 'unavailable'] as const) {
      expect(component['capacityDescription'](capacity(state) as never)).toMatch(/\.$/);
    }
  });
});
