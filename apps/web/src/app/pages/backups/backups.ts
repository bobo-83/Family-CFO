import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, DestroyRef, inject, OnInit, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type {
  BackupCapacityObservation,
  BackupConfig,
  BackupConfigUpdateRequest,
  BackupDestinationRecoveryStatus,
  BackupRecoveryStatus,
  BackupRetentionPolicy,
  BackupRetentionPolicyUpdate,
  HouseholdKeyStatus,
  RemoteBackup,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { householdSessionKey } from '../../core/household-currency.service';
import { subscribeToAuthState } from '../../core/token-store';
import { apiErrorMessage } from '../../shared/api-error';

type Frequency = 'every_15min' | 'hourly' | 'every_6h' | 'daily' | 'weekly' | 'off';
type Destination = 'local' | 'offbox';
type SaveKind = 'automatic' | 'retention';

interface RetentionDraft {
  mode: 'tiered' | 'keep_all';
  keepAllDays: number | null;
  dailyUntilDays: number | null;
  weeklyUntilDays: number | null;
  maxGB: number | null;
  reserveGB: number | null;
}

interface RequestOwner {
  sessionKey: string;
  sessionGeneration: number;
  requestGeneration: number;
  configUpdatedAt: string | null;
}

interface MutationOwner {
  sessionKey: string;
  sessionGeneration: number;
  requestGeneration: number;
}

interface ConflictState {
  kind: SaveKind;
  payload: BackupConfigUpdateRequest;
  current: BackupConfig;
}

@Component({
  selector: 'app-backups',
  templateUrl: './backups.html',
  styleUrl: './backups.scss',
  imports: [
    FormsModule,
    NgTemplateOutlet,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
})
export class Backups implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly destroyRef = inject(DestroyRef);
  private destroyed = false;

  protected readonly canManageBackups = computed(() => this.auth.hasRight('backups.manage'));
  private readonly sessionKey = signal(householdSessionKey());
  private sessionGeneration = 0;
  private configRequestGeneration = 0;
  private statusRequestGeneration = 0;
  private conflictRequestGeneration = 0;
  private remoteRequestGeneration = 0;
  private keyRequestGeneration = 0;
  private keyRevealRequestGeneration = 0;
  private recoveryKeyRequestGeneration = 0;
  private sealModeRequestGeneration = 0;
  private recoveryUnlockRequestGeneration = 0;
  private exportRequestGeneration = 0;
  private checkRequestGeneration = 0;
  private mutationRequestGeneration = 0;
  private pendingMutationConfigRefresh = false;
  private pendingMutationRemoteRefresh = false;

  // Synology SMB settings (auto-saved as they change).
  protected readonly host = signal('');
  protected readonly share = signal('');
  protected readonly folder = signal('');
  protected readonly username = signal('');
  protected readonly password = signal('');
  protected readonly domain = signal('');
  protected readonly frequency = signal<Frequency>('daily');
  protected readonly hasStoredPassword = signal(false);
  protected readonly serverConfig = signal<BackupConfig | null>(null);
  protected readonly localDraft = signal<RetentionDraft>(this.defaultRetentionDraft());
  protected readonly offboxDraft = signal<RetentionDraft>(this.defaultRetentionDraft());
  protected readonly recoveryStatus = signal<BackupRecoveryStatus | null>(null);
  protected readonly recoveryStatusError = signal<string | null>(null);
  protected readonly recoveryStatusLoading = signal(false);
  protected readonly configSaving = signal(false);
  protected readonly configConflict = signal<ConflictState | null>(null);
  private pendingAutomaticSave = false;
  private pendingRetentionSave = false;
  private saveLoop: Promise<void> | null = null;
  protected readonly revealedKey = signal<string | null>(null);
  private passwordEdited = false;

  /** Bound, not a static attribute: an interpolated `i18n-placeholder` would be
   * dropped silently, so the translated hint is built here. */
  protected readonly passwordPlaceholder = computed(() =>
    this.hasStoredPassword()
      ? $localize`:Form field placeholder|A password is already stored; an empty field keeps it:saved — leave blank to keep`
      : '',
  );

  /** Fallback when the server reports an unwritable destination without a reason. */
  protected readonly connectionFailedLabel = $localize`:Connection check result|The destination check failed and the server gave no reason:Failed`;

  protected readonly latest = signal<{
    status: string;
    completed_at?: string | null;
    size_bytes?: number | null;
    error_message?: string | null;
    remote_status?: string | null;
    remote_error?: string | null;
  } | null>(null);
  protected readonly remoteBackups = signal<RemoteBackup[]>([]);
  protected readonly remoteListError = signal<string | null>(null);

  /** Grouped by day, newest first — four snapshots a day made the flat list
   * an endless scroll (user report 2026-07-26). Mirrors the iOS grouping. */
  protected readonly remoteBackupDays = computed(() => {
    const byDay = new Map<string, RemoteBackup[]>();
    for (const backup of this.remoteBackups()) {
      const day = new Date(backup.modified_at * 1000).toDateString();
      const list = byDay.get(day) ?? [];
      list.push(backup);
      byDay.set(day, list);
    }
    return [...byDay.entries()]
      .map(([day, backups]) => ({
        day,
        label: new Date(backups[0].modified_at * 1000).toLocaleDateString(undefined, {
          month: 'short',
          day: 'numeric',
          year: 'numeric',
        }),
        backups: backups.sort((a, b) => b.modified_at - a.modified_at),
      }))
      .sort((a, b) => b.backups[0].modified_at - a.backups[0].modified_at);
  });

  protected readonly busy = signal(false);
  protected readonly checking = signal(false);
  protected readonly checkResult = signal<{
    writable: boolean;
    reason?: string | null;
    capacity?: BackupCapacityObservation;
  } | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly statusMessage = signal<string | null>(null);

  protected readonly backups = resource({
    params: () => (this.canManageBackups() ? this.sessionKey() : undefined),
    loader: async () => {
      const { data, error } = await this.api.listBackups();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load backups.`));
      }
      return data.backups;
    },
  });

  // ADR 0072 Phase 2: household data-key posture + the once-shown recovery key.
  protected readonly keyStatus = signal<HouseholdKeyStatus | null>(null);
  protected readonly generatedRecoveryKey = signal<string | null>(null);

  // "Unlock with recovery key…" — the rescue for a locked household (sealed
  // after a restart, or convenient restored without its master key). The
  // entered key lives only in this signal: never logged, never persisted.
  protected readonly showRecoveryUnlock = signal(false);
  protected readonly recoveryUnlockInput = signal('');

  constructor() {
    const unsubscribe = subscribeToAuthState(() => {
      const nextSessionKey = householdSessionKey();
      if (nextSessionKey === this.sessionKey()) {
        return;
      }
      ++this.sessionGeneration;
      this.sessionKey.set(nextSessionKey);
      this.clearSessionState();
      if (this.canManageBackups()) {
        void this.loadPageState();
      }
    });
    this.destroyRef.onDestroy(() => {
      // Component lifetime is an ownership boundary. Invalidate every pending
      // publication before unsubscribing so no late completion can seed UI or
      // trigger a browser-global side effect such as an export download.
      this.destroyed = true;
      ++this.sessionGeneration;
      this.clearSessionState();
      unsubscribe();
    });
  }

  async ngOnInit(): Promise<void> {
    this.loadRunningVersion();
    if (this.canManageBackups()) {
      await this.loadPageState();
    }
  }

  private async loadPageState(): Promise<void> {
    const loaded = await this.loadConfig();
    if (!loaded) {
      return;
    }
    await Promise.all([
      this.loadRecoveryStatus(),
      this.loadKeyStatus(),
      this.host() ? this.loadRemote(false) : Promise.resolve(),
    ]);
  }

  private clearSessionState(): void {
    ++this.configRequestGeneration;
    ++this.statusRequestGeneration;
    ++this.conflictRequestGeneration;
    ++this.remoteRequestGeneration;
    ++this.keyRequestGeneration;
    ++this.keyRevealRequestGeneration;
    ++this.recoveryKeyRequestGeneration;
    ++this.sealModeRequestGeneration;
    ++this.recoveryUnlockRequestGeneration;
    ++this.exportRequestGeneration;
    ++this.checkRequestGeneration;
    ++this.mutationRequestGeneration;
    this.pendingMutationConfigRefresh = false;
    this.pendingMutationRemoteRefresh = false;
    this.pendingAutomaticSave = false;
    this.pendingRetentionSave = false;
    this.serverConfig.set(null);
    this.configConflict.set(null);
    this.recoveryStatus.set(null);
    this.recoveryStatusError.set(null);
    this.recoveryStatusLoading.set(false);
    this.remoteBackups.set([]);
    this.remoteListError.set(null);
    this.latest.set(null);
    this.keyStatus.set(null);
    this.actionError.set(null);
    this.statusMessage.set(null);
    this.checkResult.set(null);
    this.checking.set(false);
    this.busy.set(false);
    this.exporting.set(false);
    this.exportError.set(null);
    this.revealedKey.set(null);
    this.generatedRecoveryKey.set(null);
    this.showRecoveryUnlock.set(false);
    this.recoveryUnlockInput.set('');
    this.password.set('');
    this.passwordEdited = false;
  }

  private captureOwner(requestGeneration: number): RequestOwner {
    return {
      sessionKey: this.sessionKey(),
      sessionGeneration: this.sessionGeneration,
      requestGeneration,
      configUpdatedAt: this.serverConfig()?.updated_at ?? null,
    };
  }

  private beginMutation(refresh: { config?: boolean; remote?: boolean } = {}): MutationOwner {
    const owner = {
      sessionKey: this.sessionKey(),
      sessionGeneration: this.sessionGeneration,
      requestGeneration: ++this.mutationRequestGeneration,
    };
    // A newer, different mutation must inherit refresh work that an older
    // owner can no longer publish. Otherwise restore -> create could lose the
    // restored config token, or remote delete -> local delete could leave the
    // Synology inventory stale.
    this.pendingMutationConfigRefresh ||= !!refresh.config;
    this.pendingMutationRemoteRefresh ||= !!refresh.remote;
    // The new mutation owns all action feedback and the status refresh that
    // follows it. Invalidate an older refresh immediately, rather than waiting
    // for its completion to discover that it was superseded.
    ++this.statusRequestGeneration;
    this.recoveryStatusLoading.set(false);
    this.actionError.set(null);
    this.statusMessage.set(null);
    return owner;
  }

  private ownsMutation(owner: MutationOwner): boolean {
    return (
      !this.destroyed &&
      owner.sessionKey === this.sessionKey() &&
      owner.sessionGeneration === this.sessionGeneration &&
      owner.requestGeneration === this.mutationRequestGeneration
    );
  }

  private ownsActionFeedback(mutationGeneration: number): boolean {
    return !this.destroyed && mutationGeneration === this.mutationRequestGeneration;
  }

  private owns(
    owner: RequestOwner,
    currentRequestGeneration: number,
    requireConfigToken = true,
  ): boolean {
    return (
      !this.destroyed &&
      owner.sessionKey === this.sessionKey() &&
      owner.sessionGeneration === this.sessionGeneration &&
      owner.requestGeneration === currentRequestGeneration &&
      (!requireConfigToken || owner.configUpdatedAt === (this.serverConfig()?.updated_at ?? null))
    );
  }

  /** The box's running version (same unauthenticated /health the shell footer
   * uses) — flags backups the server would refuse to restore with a 409. */
  protected readonly runningVersion = signal<string | null>(null);

  private loadRunningVersion(): void {
    void fetch('/api/v1/health')
      .then((response) => response.json())
      .then((health: { version?: string }) => {
        if (!this.destroyed) this.runningVersion.set(health.version ?? null);
      })
      .catch(() => {
        if (!this.destroyed) this.runningVersion.set(null);
      });
  }

  /** Numeric dotted-tuple compare (never string compare): true when the backup
   * was made by a NEWER app than the box runs — restore needs an update first. */
  protected isFromNewerVersion(appVersion: string | null | undefined): boolean {
    const running = this.runningVersion();
    if (!appVersion || !running) {
      return false;
    }
    const a = appVersion.split('.').map((part) => Number.parseInt(part, 10));
    const b = running.split('.').map((part) => Number.parseInt(part, 10));
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const x = a[i] ?? 0;
      const y = b[i] ?? 0;
      if (Number.isNaN(x) || Number.isNaN(y)) {
        return false;
      }
      if (x !== y) {
        return x > y;
      }
    }
    return false;
  }

  /** Bound `[title]`, for the same reason as `passwordPlaceholder`. */
  protected versionTitle(appVersion: string | null | undefined): string {
    return this.isFromNewerVersion(appVersion)
      ? $localize`:Backup version tooltip|The backup was written by a newer app than the box runs:Made by a newer version — update first`
      : '';
  }

  private async loadConfig(): Promise<boolean> {
    const generation = ++this.configRequestGeneration;
    const owner = this.captureOwner(generation);
    const { data, error } = await this.api.getBackupConfig();
    if (!this.owns(owner, this.configRequestGeneration)) {
      return false;
    }
    if (error || !data) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to load backup settings.`));
      return false;
    }
    this.applyConfig(data, true);
    this.actionError.set(null);
    return true;
  }

  private applyConfig(data: BackupConfig, applyDraft: boolean): void {
    this.serverConfig.set(data);
    this.hasStoredPassword.set(data.has_password ?? false);
    this.latest.set(data.latest ?? null);
    if (applyDraft) {
      this.host.set(data.smb_host ?? '');
      this.share.set(data.smb_share ?? '');
      this.folder.set(data.smb_folder ?? '');
      this.username.set(data.smb_username ?? '');
      this.domain.set(data.smb_domain ?? '');
      this.frequency.set((data.frequency as Frequency) ?? 'daily');
      this.localDraft.set(this.draftFromConfig(data, 'local'));
      this.offboxDraft.set(this.draftFromConfig(data, 'offbox'));
      this.password.set('');
      this.passwordEdited = false;
    }
  }

  protected onPasswordInput(): void {
    this.passwordEdited = true;
  }

  /** Existing cadence/destination fields may save on blur, but every write uses
   * one serialized lane. Repeated blur events coalesce to the newest draft. */
  protected async saveConfig(): Promise<void> {
    this.pendingAutomaticSave = true;
    await this.ensureSaveLoop();
  }

  protected async saveAndActivateRetention(): Promise<void> {
    if (this.retentionValidation()) {
      return;
    }
    this.pendingRetentionSave = true;
    await this.ensureSaveLoop();
  }

  private ensureSaveLoop(): Promise<void> {
    if (!this.saveLoop) {
      this.saveLoop = this.runSaveLoop().finally(() => {
        this.saveLoop = null;
      });
    }
    return this.saveLoop;
  }

  private async runSaveLoop(): Promise<void> {
    this.configSaving.set(true);
    try {
      while (!this.configConflict() && (this.pendingAutomaticSave || this.pendingRetentionSave)) {
        if (this.pendingAutomaticSave) {
          this.pendingAutomaticSave = false;
          await this.performSave('automatic', this.automaticPayload());
          continue;
        }
        this.pendingRetentionSave = false;
        await this.performSave('retention', this.retentionPayload());
      }
    } finally {
      this.configSaving.set(false);
    }
  }

  private automaticPayload(): BackupConfigUpdateRequest {
    return {
      frequency: this.frequency(),
      smb_host: this.host() || null,
      smb_share: this.share() || null,
      smb_folder: this.folder() || null,
      smb_username: this.username() || null,
      smb_password: this.passwordEdited ? this.password() : undefined,
      smb_domain: this.domain() || null,
    };
  }

  private retentionPayload(): BackupConfigUpdateRequest {
    return {
      local_retention: this.policyUpdate(this.localDraft()),
      offbox_retention: this.policyUpdate(this.offboxDraft()),
      local_max_bytes: this.gbToOptionalBytes(this.localDraft().maxGB),
      offbox_max_bytes: this.gbToOptionalBytes(this.offboxDraft().maxGB),
      local_min_free_bytes: this.gbToRequiredBytes(this.localDraft().reserveGB),
      offbox_min_free_bytes: this.gbToRequiredBytes(this.offboxDraft().reserveGB),
      confirm_retention_policy: true,
    };
  }

  private async performSave(
    kind: SaveKind,
    draftPayload: BackupConfigUpdateRequest,
  ): Promise<void> {
    const config = this.serverConfig();
    if (!config) {
      return;
    }
    ++this.configRequestGeneration;
    const owner = this.captureOwner(this.configRequestGeneration);
    const feedbackGeneration = this.mutationRequestGeneration;
    const payload = { ...draftPayload, expected_updated_at: config.updated_at };
    const { data, error, response } = await this.api.updateBackupConfig(payload);
    if (!this.owns(owner, this.configRequestGeneration)) {
      return;
    }
    if (error || !data) {
      if (response?.status === 409) {
        await this.loadConflict(kind, draftPayload, owner, feedbackGeneration);
      } else if (this.ownsActionFeedback(feedbackGeneration)) {
        this.actionError.set(apiErrorMessage(error, $localize`Failed to save settings.`));
      }
      return;
    }
    this.configConflict.set(null);
    // The response advances the CAS token, but it must not overwrite edits made
    // while this request was in flight. The preserved signals become the next
    // coalesced write (or remain an explicit retention draft).
    this.applyConfig(data, false);
    if (
      kind === 'automatic' &&
      draftPayload.smb_password !== undefined &&
      this.passwordEdited &&
      this.password() === draftPayload.smb_password
    ) {
      this.password.set('');
      this.passwordEdited = false;
    }
    if (this.ownsActionFeedback(feedbackGeneration)) {
      this.actionError.set(null);
      this.statusMessage.set(
        kind === 'retention'
          ? $localize`:Status message|Retention policy was saved and activated:Retention policy saved and activated. Pruning runs during independent maintenance.`
          : $localize`:Status message|Backup destination or schedule settings were saved:Backup settings saved.`,
      );
      await this.loadRecoveryStatus();
    }
  }

  private async loadConflict(
    kind: SaveKind,
    payload: BackupConfigUpdateRequest,
    saveOwner: RequestOwner,
    feedbackGeneration: number,
  ): Promise<void> {
    const generation = ++this.conflictRequestGeneration;
    const { data } = await this.api.getBackupConfig();
    if (
      saveOwner.sessionKey !== this.sessionKey() ||
      saveOwner.sessionGeneration !== this.sessionGeneration ||
      generation !== this.conflictRequestGeneration ||
      !data
    ) {
      return;
    }
    this.configConflict.set({ kind, payload, current: data });
    if (this.ownsActionFeedback(feedbackGeneration)) {
      this.actionError.set(
        $localize`:Configuration conflict|Another administrator changed backup settings while this draft was open:Backup configuration changed elsewhere. Your draft is preserved; choose how to reconcile it.`,
      );
    }
    this.pendingAutomaticSave = false;
    this.pendingRetentionSave = false;
  }

  protected useCurrentConflictConfig(): void {
    const conflict = this.configConflict();
    if (!conflict) return;
    this.applyConfig(conflict.current, true);
    this.configConflict.set(null);
    this.actionError.set(null);
    void this.loadRecoveryStatus();
  }

  protected async retryConflictDraft(): Promise<void> {
    const conflict = this.configConflict();
    if (!conflict) return;
    this.serverConfig.set(conflict.current);
    this.configConflict.set(null);
    this.actionError.set(null);
    const currentDraft =
      conflict.kind === 'automatic' ? this.automaticPayload() : this.retentionPayload();
    this.configSaving.set(true);
    try {
      await this.performSave(conflict.kind, currentDraft);
    } finally {
      this.configSaving.set(false);
    }
  }

  private async loadRecoveryStatus(mutationOwner?: MutationOwner): Promise<void> {
    if (mutationOwner && !this.ownsMutation(mutationOwner)) {
      return;
    }
    const generation = ++this.statusRequestGeneration;
    const owner = this.captureOwner(generation);
    this.recoveryStatusLoading.set(true);
    const { data, error } = await this.api.getBackupRecoveryStatus();
    if (
      !this.owns(owner, this.statusRequestGeneration) ||
      (mutationOwner && !this.ownsMutation(mutationOwner))
    ) {
      return;
    }
    this.recoveryStatusLoading.set(false);
    if (error || !data) {
      // A current failure clears stale dates. Existing backup actions remain usable.
      this.recoveryStatus.set(null);
      this.recoveryStatusError.set(
        apiErrorMessage(
          error,
          $localize`:Recovery status error|The recovery observation endpoint could not be read:Recovery status unavailable.`,
        ),
      );
      return;
    }
    this.recoveryStatus.set(data);
    this.recoveryStatusError.set(null);
  }

  protected async refreshRecoveryStatus(): Promise<void> {
    await this.loadRecoveryStatus();
  }

  protected canTest = computed(
    () =>
      !!this.host() &&
      !!this.share() &&
      !!this.username() &&
      (this.passwordEdited || this.hasStoredPassword()),
  );

  protected async testConnection(): Promise<void> {
    const generation = ++this.checkRequestGeneration;
    const owner = this.captureOwner(generation);
    const feedbackGeneration = this.mutationRequestGeneration;
    this.checking.set(true);
    this.checkResult.set(null);
    const { data, error } = await this.api.checkBackupDestination({
      smb_host: this.host(),
      smb_share: this.share(),
      smb_folder: this.folder() || undefined,
      smb_username: this.username(),
      smb_password: this.passwordEdited ? this.password() : undefined,
      smb_domain: this.domain() || undefined,
    });
    if (!this.owns(owner, this.checkRequestGeneration, false)) {
      return;
    }
    this.checking.set(false);
    if (error) {
      if (this.ownsActionFeedback(feedbackGeneration)) {
        this.actionError.set(apiErrorMessage(error, $localize`Failed to test connection.`));
      }
    } else {
      if (this.ownsActionFeedback(feedbackGeneration)) {
        this.actionError.set(null);
      }
      this.checkResult.set(data ?? null);
    }
    if (this.ownsActionFeedback(feedbackGeneration)) {
      await this.loadRecoveryStatus();
    }
  }

  private async loadRemote(refreshStatus = true, mutationOwner?: MutationOwner): Promise<void> {
    if (mutationOwner && !this.ownsMutation(mutationOwner)) {
      return;
    }
    const generation = ++this.remoteRequestGeneration;
    const owner = this.captureOwner(generation);
    const { data, error } = await this.api.listRemoteBackups();
    if (
      !this.owns(owner, this.remoteRequestGeneration) ||
      (mutationOwner && !this.ownsMutation(mutationOwner))
    ) {
      return;
    }
    if (!error && data) {
      this.remoteBackups.set(data.backups);
      this.remoteListError.set(
        data.status === 'unavailable'
          ? (data.reason ??
              $localize`:Recovery state detail|Synology listing failed:Synology inventory is unavailable, so its recovery window is unknown.`)
          : null,
      );
    } else {
      this.remoteBackups.set([]);
      this.remoteListError.set(apiErrorMessage(error, $localize`Failed to load backups.`));
    }
    if (refreshStatus) {
      await this.loadRecoveryStatus(mutationOwner);
    }
  }

  private async refreshConfigAfterMutation(mutationOwner: MutationOwner): Promise<void> {
    if (!this.ownsMutation(mutationOwner)) {
      return;
    }
    const generation = ++this.configRequestGeneration;
    const owner = this.captureOwner(generation);
    const { data } = await this.api.getBackupConfig();
    if (
      this.owns(owner, this.configRequestGeneration) &&
      this.ownsMutation(mutationOwner) &&
      data
    ) {
      // Restore rotates destination generations and re-enables policy review.
      // Advance metadata/CAS state without discarding an unsaved local draft.
      this.applyConfig(data, false);
    }
  }

  private async refreshAfterMutation(mutationOwner: MutationOwner): Promise<void> {
    if (!this.ownsMutation(mutationOwner)) return;
    if (this.pendingMutationConfigRefresh) {
      await this.refreshConfigAfterMutation(mutationOwner);
      if (!this.ownsMutation(mutationOwner)) return;
      this.pendingMutationConfigRefresh = false;
    }
    if (this.pendingMutationRemoteRefresh) {
      await this.loadRemote(false, mutationOwner);
      if (!this.ownsMutation(mutationOwner)) return;
      this.pendingMutationRemoteRefresh = false;
    }
    await this.loadRecoveryStatus(mutationOwner);
  }

  protected async createBackup(): Promise<void> {
    if (this.busy()) return;
    const owner = this.beginMutation({ remote: true });
    this.busy.set(true);
    const { data, error } = await this.api.createBackup();
    if (!this.ownsMutation(owner)) return;
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to create backup.`));
    } else if (data) {
      this.latest.set(data);
      if (data.status === 'completed') {
        this.actionError.set(null);
        this.statusMessage.set(
          $localize`:Status message|A backup finished successfully:Backup complete.`,
        );
      } else if (data.status === 'failed') {
        this.statusMessage.set(null);
        this.actionError.set(
          data.error_message
            ? $localize`:Backup failure|The server returned a failed backup job:Last backup failed: ${data.error_message}:errorMessage:.`
            : $localize`:Backup failure|The server returned a failed backup job without a reason:Last backup failed.`,
        );
      } else {
        // A future asynchronous endpoint may return a nonterminal job. Do not
        // turn transport success into a false completion claim.
        this.actionError.set(null);
        this.statusMessage.set(null);
      }
    }
    // A lost response can hide a committed backup, so both inventories and the
    // recovery observation refresh after success or failure.
    this.backups.reload();
    await this.refreshAfterMutation(owner);
  }

  protected async restore(id: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an on-box backup is restored over the live data:Restore this backup? This REPLACES all current data with the backup contents. This cannot be undone.`,
      )
    )
      return;
    const owner = this.beginMutation({ config: true });
    this.busy.set(true);
    const { error } = await this.api.restoreBackup(id);
    if (!this.ownsMutation(owner)) return;
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to restore backup.`));
    } else {
      this.statusMessage.set(
        $localize`:Status message|An on-box backup was restored:On-box backup restored.`,
      );
    }
    this.backups.reload();
    await this.refreshAfterMutation(owner);
  }

  protected async restoreRemote(filename: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an off-box snapshot is restored over the live data:Restore from ${filename}:filename:? This REPLACES all current data with the backup contents. This cannot be undone.`,
      )
    )
      return;
    const owner = this.beginMutation({ config: true, remote: true });
    this.busy.set(true);
    const { error } = await this.api.restoreRemoteBackup(filename);
    if (!this.ownsMutation(owner)) return;
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to restore from Synology.`));
    } else {
      this.statusMessage.set(
        $localize`:Status message|An off-box snapshot was restored:Restored from ${filename}:filename:.`,
      );
    }
    await this.refreshAfterMutation(owner);
  }

  protected async revealKey(): Promise<void> {
    const generation = ++this.keyRevealRequestGeneration;
    const owner = this.captureOwner(generation);
    const { data, error } = await this.api.getBackupEncryptionKey();
    if (!this.owns(owner, this.keyRevealRequestGeneration, false)) {
      return;
    }
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to load key.`));
      return;
    }
    this.revealedKey.set(
      data?.key ??
        $localize`:Backup key value|Shown in place of the key when the box has none:(not configured)`,
    );
  }

  protected async copyKey(): Promise<void> {
    const key = this.revealedKey();
    if (key) {
      await navigator.clipboard?.writeText(key);
      this.statusMessage.set(
        $localize`:Status message|The backup key was put on the clipboard:Backup key copied.`,
      );
    }
  }

  private async loadKeyStatus(): Promise<void> {
    const generation = ++this.keyRequestGeneration;
    const owner = this.captureOwner(generation);
    const { data } = await this.api.getHouseholdKeyStatus();
    if (this.owns(owner, this.keyRequestGeneration, false)) {
      this.keyStatus.set(data ?? null);
    }
  }

  protected async generateRecoveryKey(): Promise<void> {
    if (this.busy()) return;
    if (
      this.keyStatus()?.has_recovery_key &&
      !confirm(
        $localize`:Confirmation|Browser confirm before the recovery key is replaced:Replace the recovery key? The old recovery key stops working immediately.`,
      )
    ) {
      return;
    }
    const generation = ++this.recoveryKeyRequestGeneration;
    const owner = this.captureOwner(generation);
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.generateRecoveryKey();
    if (!this.owns(owner, this.recoveryKeyRequestGeneration, false)) {
      return;
    }
    if (error) {
      this.busy.set(false);
      // 409 (encryption off) carries a human message — show it verbatim.
      this.actionError.set(apiErrorMessage(error, $localize`Failed to create recovery key.`));
      return;
    }
    // This is the only response carrying the one-time secret. Publish it as
    // soon as this request proves ownership; the best-effort posture refresh
    // must never delay or lose the user's sole chance to save it.
    this.generatedRecoveryKey.set(data?.recovery_key ?? null);
    await this.loadKeyStatus();
    if (!this.owns(owner, this.recoveryKeyRequestGeneration, false)) {
      return;
    }
    this.busy.set(false);
  }

  protected async copyRecoveryKey(): Promise<void> {
    const key = this.generatedRecoveryKey();
    if (key) {
      await navigator.clipboard?.writeText(key);
      this.statusMessage.set(
        $localize`:Status message|The recovery key was put on the clipboard:Recovery key copied.`,
      );
    }
  }

  /** ADR 0072 Phase 3: convenient ↔ sealed. The confirm restates the
   * consequence in one sentence; a 409 carries the server's human message
   * (missing member key / recovery key, or locked) — show it verbatim. */
  protected async setSealMode(mode: 'convenient' | 'sealed'): Promise<void> {
    if (this.busy()) return;
    const consequence =
      mode === 'sealed'
        ? $localize`:Confirmation|Browser confirm before the household is sealed@@sealHouseholdConfirmation:Seal this household? After a restart, nothing is readable until someone signs in. Unattended sync, snapshots and study then run while the in-memory key session is open. It expires 30 minutes after its last member-driven use; signing out does not close it immediately.`
        : $localize`:Confirmation|Browser confirm before the household leaves sealed mode:Switch back to convenient? The box keeps a spare of your data key again, so overnight work runs without anyone signed in.`;
    if (!confirm(consequence)) return;
    const generation = ++this.sealModeRequestGeneration;
    const owner = this.captureOwner(generation);
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.setSealMode(mode);
    if (!this.owns(owner, this.sealModeRequestGeneration, false)) {
      return;
    }
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to switch privacy mode.`));
      return;
    }
    this.keyStatus.set(data ?? null);
  }

  /** Unlock a locked household with its recovery key. On 200 the returned
   * status replaces keyStatus (unlocked — the locked line disappears; the
   * server also silently heals a stale box wrap after a fresh-hardware
   * restore). A 400 carries the server's human message ("doesn't match") —
   * shown verbatim, and the input stays open for another try. */
  protected async unlockWithRecoveryKey(): Promise<void> {
    if (this.busy()) return;
    const key = this.recoveryUnlockInput().trim();
    if (!key) return;
    const generation = ++this.recoveryUnlockRequestGeneration;
    const owner = this.captureOwner(generation);
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.unlockWithRecoveryKey(key);
    if (!this.owns(owner, this.recoveryUnlockRequestGeneration, false)) {
      return;
    }
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to unlock.`));
      return;
    }
    this.keyStatus.set(data ?? null);
    this.recoveryUnlockInput.set('');
    this.showRecoveryUnlock.set(false);
    this.statusMessage.set(
      $localize`:Status message|A locked household was unlocked with its recovery key:Household unlocked.`,
    );
  }

  // --- "Export my data" (#189): the whole household as a portable zip ---
  protected readonly exporting = signal(false);
  protected readonly exportError = signal<string | null>(null);

  /** Direct authed fetch (binary, not the typed client); on success a browser
   * download is triggered. A 423 (sealed household, locked) carries the
   * server's human message — shown inline, verbatim. */
  protected async exportData(): Promise<void> {
    if (this.exporting()) return;
    const generation = ++this.exportRequestGeneration;
    const owner = this.captureOwner(generation);
    this.exporting.set(true);
    this.exportError.set(null);
    try {
      const { blob, filename } = await this.api.downloadHouseholdExport();
      if (!this.owns(owner, this.exportRequestGeneration, false)) {
        return;
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (!this.owns(owner, this.exportRequestGeneration, false)) {
        return;
      }
      this.exportError.set(
        error instanceof Error
          ? error.message
          : $localize`:Error message|The household export could not be produced:Export failed.`,
      );
    } finally {
      if (this.owns(owner, this.exportRequestGeneration, false)) {
        this.exporting.set(false);
      }
    }
  }

  protected recoveryKeyCreatedLabel(iso: string | null | undefined): string {
    if (!iso) {
      return '';
    }
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  private defaultRetentionDraft(): RetentionDraft {
    return {
      mode: 'tiered',
      keepAllDays: 3,
      dailyUntilDays: 14,
      weeklyUntilDays: 90,
      maxGB: 0,
      reserveGB: 1,
    };
  }

  private draftFromConfig(config: BackupConfig, destination: Destination): RetentionDraft {
    const fallback = this.defaultRetentionDraft();
    const policy = destination === 'local' ? config.local_retention : config.offbox_retention;
    const maxBytes = destination === 'local' ? config.local_max_bytes : config.offbox_max_bytes;
    const reserveBytes =
      destination === 'local' ? config.local_min_free_bytes : config.offbox_min_free_bytes;
    return {
      mode: policy?.mode ?? fallback.mode,
      keepAllDays: policy?.keep_all_days ?? fallback.keepAllDays,
      dailyUntilDays: policy?.daily_until_days ?? fallback.dailyUntilDays,
      weeklyUntilDays: policy?.weekly_until_days ?? fallback.weeklyUntilDays,
      maxGB: maxBytes == null ? 0 : maxBytes / 1_000_000_000,
      reserveGB: reserveBytes == null ? fallback.reserveGB : reserveBytes / 1_000_000_000,
    };
  }

  protected updateDraft(destination: Destination, patch: Partial<RetentionDraft>): void {
    const target = destination === 'local' ? this.localDraft : this.offboxDraft;
    target.update((draft) => ({ ...draft, ...patch }));
  }

  private validateDestinationDraft(draft: RetentionDraft): string | null {
    if (draft.mode === 'tiered') {
      const values = [draft.keepAllDays, draft.dailyUntilDays, draft.weeklyUntilDays];
      if (values.some((value) => value == null || !Number.isInteger(value))) {
        return $localize`:Retention validation|Tier horizons must be whole numbers:Enter whole numbers for all retention horizons.`;
      }
      const [keepAll, daily, weekly] = values as number[];
      if (keepAll < 1 || weekly > 3650) {
        return $localize`:Retention validation|Allowed policy horizon range:Retention horizons must be between 1 and 3650 days.`;
      }
      if (keepAll > daily || daily > weekly) {
        return $localize`:Retention validation|Tier horizons must be cumulative:Keep-everything days must be no greater than daily days, which must be no greater than weekly days.`;
      }
    }
    if (draft.maxGB == null || !Number.isFinite(draft.maxGB) || draft.maxGB < 0) {
      return $localize`:Capacity validation|Maximum logical size must be non-negative:Maximum total size must be zero or greater.`;
    }
    if (draft.reserveGB == null || !Number.isFinite(draft.reserveGB) || draft.reserveGB < 0) {
      return $localize`:Capacity validation|Free-space reserve must be non-negative:Minimum free space must be zero or greater.`;
    }
    return null;
  }

  protected readonly localDraftError = computed(() =>
    this.validateDestinationDraft(this.localDraft()),
  );
  protected readonly offboxDraftError = computed(() =>
    this.validateDestinationDraft(this.offboxDraft()),
  );
  protected readonly retentionValidation = computed(
    () => this.localDraftError() ?? this.offboxDraftError(),
  );

  protected readonly retentionChanged = computed(() => {
    const config = this.serverConfig();
    if (!config) return false;
    const payload = this.retentionPayload();
    return (
      JSON.stringify(payload.local_retention) !==
        JSON.stringify(this.policyUpdate(this.draftFromConfig(config, 'local'))) ||
      JSON.stringify(payload.offbox_retention) !==
        JSON.stringify(this.policyUpdate(this.draftFromConfig(config, 'offbox'))) ||
      payload.local_max_bytes !== config.local_max_bytes ||
      payload.offbox_max_bytes !== config.offbox_max_bytes ||
      payload.local_min_free_bytes !== config.local_min_free_bytes ||
      payload.offbox_min_free_bytes !== config.offbox_min_free_bytes ||
      config.retention_review_required
    );
  });

  private policyUpdate(draft: RetentionDraft): BackupRetentionPolicyUpdate {
    return draft.mode === 'keep_all'
      ? { mode: 'keep_all', keep_all_days: null, daily_until_days: null, weekly_until_days: null }
      : {
          mode: 'tiered',
          keep_all_days: draft.keepAllDays,
          daily_until_days: draft.dailyUntilDays,
          weekly_until_days: draft.weeklyUntilDays,
        };
  }

  private gbToOptionalBytes(value: number | null): number | null {
    return value && value > 0 ? Math.round(value * 1_000_000_000) : null;
  }

  private gbToRequiredBytes(value: number | null): number {
    return Math.round((value ?? 0) * 1_000_000_000);
  }

  protected retentionSummary(draft: RetentionDraft): string {
    if (draft.mode === 'keep_all') {
      return $localize`:Retention policy summary|No time-based pruning:Keep every backup; the optional size limit still applies.`;
    }
    return $localize`:Retention policy summary|Cumulative tier horizons:Every backup for ${draft.keepAllDays}:keepAllDays: days · one daily through ${draft.dailyUntilDays}:dailyUntilDays: days · one weekly through ${draft.weeklyUntilDays}:weeklyUntilDays: days.`;
  }

  protected policySummary(policy: BackupRetentionPolicy): string {
    return this.retentionSummary({
      mode: policy.mode,
      keepAllDays: policy.keep_all_days,
      dailyUntilDays: policy.daily_until_days,
      weeklyUntilDays: policy.weekly_until_days,
      maxGB: 0,
      reserveGB: 0,
    });
  }

  protected policyTarget(policy: BackupRetentionPolicy): string {
    return policy.mode === 'keep_all'
      ? $localize`:Recovery policy target|Keep-all has no time horizon:No time-based target`
      : this.formatIsoDate(policy.target_oldest_at);
  }

  protected destinationStateLabel(status: BackupDestinationRecoveryStatus['status']): string {
    switch (status) {
      case 'not_configured':
        return $localize`:Recovery destination state|Destination has not been set up:Not configured`;
      case 'empty':
        return $localize`:Recovery destination state|Inventory succeeded and no readable archives are visible:Empty`;
      case 'healthy':
        return $localize`:Recovery destination state|Destination has a healthy recovery observation:Healthy`;
      case 'constrained':
        return $localize`:Recovery destination state|Capacity or retention limits constrain recovery:Constrained`;
      case 'degraded':
        return $localize`:Recovery destination state|Anomalies or incomplete evidence degrade recovery:Degraded`;
      case 'unavailable':
        return $localize`:Recovery destination state|Destination inventory cannot be reached:Unavailable`;
    }
  }

  protected destinationStateDescription(status: BackupDestinationRecoveryStatus): string {
    switch (status.status) {
      case 'not_configured':
        return status.destination === 'offbox'
          ? $localize`:Recovery state detail|No Synology target exists:No off-box destination is configured.`
          : $localize`:Recovery state detail|The local destination does not exist:No on-box destination is configured.`;
      case 'empty':
        return $localize`:Recovery state detail|Qualified inventory is empty:No readable backups are currently visible.`;
      case 'healthy':
        return $localize`:Recovery state detail|Inventory and capacity observations are healthy:Recovery candidates are visible and no destination problem was observed.`;
      case 'constrained':
        return $localize`:Recovery state detail|Storage limits affect the target:Storage capacity or configured limits constrain this destination.`;
      case 'degraded':
        return $localize`:Recovery state detail|Evidence is incomplete or anomalous:Recovery evidence is degraded; review the details below.`;
      case 'unavailable':
        return status.destination === 'offbox'
          ? $localize`:Recovery state detail|Synology listing failed:Synology inventory is unavailable, so its recovery window is unknown.`
          : $localize`:Recovery state detail|Local listing failed:On-box inventory is unavailable, so its recovery window is unknown.`;
    }
  }

  protected coverageLabel(status: BackupDestinationRecoveryStatus['coverage_status']): string {
    switch (status) {
      case 'not_applicable':
        return $localize`:Recovery coverage state|Coverage target does not apply:Not applicable`;
      case 'empty':
        return $localize`:Recovery coverage state|No recovery candidates exist:Empty`;
      case 'building':
        return $localize`:Recovery coverage state|History has not matured to the target yet:Building`;
      case 'met':
        return $localize`:Recovery coverage state|Qualified evidence reaches the target bucket:Target observed`;
      case 'incomplete':
        return $localize`:Recovery coverage state|Mature history does not reach the target:Incomplete`;
      case 'shortened':
        return $localize`:Recovery coverage state|Capacity or pruning shortened history:Shortened`;
      case 'unknown':
        return $localize`:Recovery coverage state|Evidence cannot establish coverage:Unknown`;
    }
  }

  protected coverageDescription(
    status: BackupDestinationRecoveryStatus['coverage_status'],
  ): string {
    switch (status) {
      case 'not_applicable':
        return $localize`:Recovery coverage detail|No configured target applies:Coverage does not apply to this destination.`;
      case 'empty':
        return $localize`:Recovery coverage detail|No qualified archives are visible:No readable backups are currently visible.`;
      case 'building':
        return $localize`:Recovery coverage detail|The configured horizon has not elapsed yet:Coverage is still building toward the configured target.`;
      case 'met':
        return $localize`:Recovery coverage detail|The outer target bucket has a qualified candidate:A readable recovery candidate was observed in the configured target bucket.`;
      case 'incomplete':
        return $localize`:Recovery coverage detail|History is mature but sparse or failing:The visible recovery history does not reach the configured target.`;
      case 'shortened':
        return $localize`:Recovery coverage detail|A limit caused qualified history to be removed:Storage limits shortened the configured recovery window.`;
      case 'unknown':
        return $localize`:Recovery coverage detail|Bounded probes or anomalies prevent a conclusion:Available evidence cannot establish the configured recovery window.`;
    }
  }

  protected probeDescription(status: BackupDestinationRecoveryStatus): string {
    switch (status.probe_status) {
      case 'complete':
        return $localize`:Recovery probe state|The endpoint qualification pass completed:Read probe complete.`;
      case 'partial':
        return $localize`:Recovery probe state|Only part of the inventory was qualified:Read probe partial; endpoint readability is not fully known.`;
      case 'unavailable':
        return $localize`:Recovery probe state|No endpoint could be qualified:Read probe unavailable; recovery dates are unknown.`;
    }
  }

  protected capacityDescription(capacity: BackupCapacityObservation): string {
    switch (capacity.status) {
      case 'ok':
        return $localize`:Backup capacity state|Observed headroom is within policy:Capacity observation is within the configured reserve.`;
      case 'warning':
        return $localize`:Backup capacity state|Observed headroom is near the warning buffer:Available space is approaching the configured reserve.`;
      case 'insufficient':
        return $localize`:Backup capacity state|Observed headroom cannot accept the estimate:Available space cannot cover the reserve and estimated next backup.`;
      case 'unknown':
        return $localize`:Backup capacity state|This destination cannot report capacity:Capacity could not be measured; backups will still be attempted.`;
      case 'unavailable':
        return $localize`:Backup capacity state|Capacity could not be queried because the destination failed:Capacity is unavailable; an attempted backup may fail.`;
    }
  }

  protected capacitySummary(capacity: BackupCapacityObservation): string {
    const reserve = this.formatBytes(capacity.reserve_bytes);
    const estimate =
      capacity.estimated_next_backup_bytes == null
        ? $localize`:Capacity summary|No next-backup estimate is available:next backup estimate unknown`
        : $localize`:Capacity summary|Estimated size of the next archive:next backup estimate ${this.formatBytes(capacity.estimated_next_backup_bytes)}:estimate:`;
    if (capacity.available_bytes != null && capacity.total_bytes != null) {
      return $localize`:Capacity summary|Caller-visible storage totals and policy headroom:${this.formatBytes(capacity.available_bytes)}:available: available of ${this.formatBytes(capacity.total_bytes)}:total: · ${estimate}:estimate: · ${reserve}:reserve: reserved.`;
    }
    if (capacity.available_bytes != null) {
      return $localize`:Capacity summary|Caller-visible storage headroom without a total:${this.formatBytes(capacity.available_bytes)}:available: available · ${estimate}:estimate: · ${reserve}:reserve: reserved.`;
    }
    return $localize`:Capacity summary|Storage totals are not measurable:${estimate}:estimate: · ${reserve}:reserve: reserved.`;
  }

  protected recoveryRole(status: BackupDestinationRecoveryStatus): 'alert' | 'status' {
    return status.status === 'constrained' || status.status === 'unavailable' ? 'alert' : 'status';
  }

  protected formatIsoDate(iso: string | null): string {
    return iso
      ? new Date(iso).toLocaleString()
      : $localize`:Unknown date|A qualified recovery date is unavailable:Unknown`;
  }

  protected formatBytes(bytes: number | null): string {
    if (bytes == null) return $localize`:Unknown size|A byte count is unavailable:Unknown`;
    if (bytes === 0) return '0 GB';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1000)), units.length - 1);
    const value = bytes / 1000 ** exponent;
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)} ${units[exponent]}`;
  }

  protected async deleteLocal(id: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an on-box backup is deleted:Delete this on-box backup?`,
      )
    )
      return;
    const owner = this.beginMutation();
    this.busy.set(true);
    const { error } = await this.api.deleteBackup(id);
    if (!this.ownsMutation(owner)) return;
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to delete backup.`));
    } else {
      this.statusMessage.set(
        $localize`:Status message|An on-box backup was deleted:On-box backup deleted.`,
      );
    }
    // Response loss can conceal a committed delete. Always reload the local
    // inventory and recovery observation for the current mutation owner.
    this.backups.reload();
    await this.refreshAfterMutation(owner);
  }

  protected async deleteRemote(filename: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an off-box snapshot is deleted:Delete ${filename}:filename: from the Synology?`,
      )
    )
      return;
    const owner = this.beginMutation({ remote: true });
    this.busy.set(true);
    const { error } = await this.api.deleteRemoteBackup(filename);
    if (!this.ownsMutation(owner)) return;
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to delete from Synology.`));
    } else {
      this.statusMessage.set(
        $localize`:Status message|An off-box snapshot was deleted:Synology backup deleted.`,
      );
    }
    // Refresh after either outcome because the server may have committed the
    // delete before its response was lost.
    await this.refreshAfterMutation(owner);
  }

  protected formatDate(epoch: number): string {
    return new Date(epoch * 1000).toLocaleString();
  }
}
