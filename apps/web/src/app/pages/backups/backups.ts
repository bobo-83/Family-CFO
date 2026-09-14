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
type SaveKind = 'automatic' | 'activation';
type OperationalSaveOrigin = 'schedule' | 'destination';
type SaveFailureClass = 'conflict' | 'transport' | 'authentication' | 'validation' | 'precondition';
type ReconciliationPhase = 'refreshing' | 'refreshFailed' | 'ready';

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
  configRevision: string | null;
}

interface MutationOwner {
  sessionKey: string;
  sessionGeneration: number;
  requestGeneration: number;
}

interface SaveOwner {
  sessionKey: string;
  sessionGeneration: number;
  presentationGeneration: number;
}

interface SaveIntent {
  id: number;
  kind: SaveKind;
  owner: SaveOwner;
  payload: BackupConfigUpdateRequest;
  origins: OperationalSaveOrigin[];
  passwordGeneration: number | null;
  confirmedRetention: BackupConfigUpdateRequest | null;
}

interface BlockedSave {
  intent: SaveIntent;
  phase: ReconciliationPhase;
  failureClass: SaveFailureClass;
  message: string;
  current: BackupConfig | null;
}

interface ConfigSaveFeedback {
  owner: SaveOwner;
  intentId: number;
  level: 'success' | 'error' | 'info';
  message: string;
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
  protected readonly blockedSave = signal<BlockedSave | null>(null);
  protected readonly inFlightSave = signal<SaveIntent | null>(null);
  protected readonly scheduleSaveFeedback = signal<ConfigSaveFeedback | null>(null);
  protected readonly destinationSaveFeedback = signal<ConfigSaveFeedback | null>(null);
  protected readonly retentionSaveFeedback = signal<ConfigSaveFeedback | null>(null);
  private pendingAutomatic: SaveIntent | null = null;
  private pendingActivation: SaveIntent | null = null;
  private saveLoop: Promise<void> | null = null;
  private saveRunGeneration = 0;
  private savePresentationGeneration = 0;
  private nextSaveIntentId = 0;
  protected readonly revealedKey = signal<string | null>(null);
  private passwordEdited = false;
  private passwordGeneration = 0;

  protected activationPending(): boolean {
    return (
      !!this.pendingActivation ||
      this.inFlightSave()?.kind === 'activation' ||
      (this.blockedSave()?.intent.kind === 'automatic' && !!this.pendingActivation)
    );
  }

  protected requiresNewActivationConfirmation(): boolean {
    const pending = this.pendingActivation;
    if (
      pending &&
      this.ownsSaveOwner(pending.owner) &&
      !this.retentionMatches(pending.confirmedRetention)
    ) {
      return true;
    }
    const blocked = this.blockedSave();
    return (
      !!blocked &&
      blocked.intent.kind === 'activation' &&
      !this.retentionMatches(blocked.intent.confirmedRetention)
    );
  }

  protected canRetryBlockedSave(): boolean {
    const blocked = this.blockedSave();
    if (
      !blocked ||
      blocked.phase !== 'ready' ||
      !blocked.current ||
      (blocked.failureClass !== 'conflict' &&
        blocked.failureClass !== 'transport' &&
        blocked.failureClass !== 'authentication')
    ) {
      return false;
    }
    if (blocked.intent.kind === 'automatic') {
      const pending = this.pendingActivation;
      return (
        !pending ||
        (this.sameSaveOwner(pending.owner, blocked.intent.owner) &&
          this.retentionMatches(pending.confirmedRetention))
      );
    }
    return this.retentionMatches(blocked.intent.confirmedRetention);
  }

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

  private invalidateConfigSaveState(): void {
    ++this.savePresentationGeneration;
    ++this.saveRunGeneration;
    ++this.conflictRequestGeneration;
    this.saveLoop = null;
    this.pendingAutomatic = null;
    this.pendingActivation = null;
    this.inFlightSave.set(null);
    this.blockedSave.set(null);
    this.configSaving.set(false);
    this.scheduleSaveFeedback.set(null);
    this.destinationSaveFeedback.set(null);
    this.retentionSaveFeedback.set(null);
    this.password.set('');
    this.passwordEdited = false;
    ++this.passwordGeneration;
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
    this.invalidateConfigSaveState();
    this.serverConfig.set(null);
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
  }

  private captureOwner(requestGeneration: number): RequestOwner {
    return {
      sessionKey: this.sessionKey(),
      sessionGeneration: this.sessionGeneration,
      requestGeneration,
      configRevision: this.serverConfig()?.revision ?? null,
    };
  }

  private beginMutation(refresh: { config?: boolean; remote?: boolean } = {}): MutationOwner {
    if (refresh.config) {
      // Restore is an ownership boundary for destructive configuration consent.
      this.invalidateConfigSaveState();
    }
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
      (!requireConfigToken || owner.configRevision === (this.serverConfig()?.revision ?? null))
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
    // Every full load replaces the presentation and invalidates unsent consent.
    this.invalidateConfigSaveState();
    this.serverConfig.set(null);
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
    ++this.passwordGeneration;
    this.clearSuccessfulFeedback('destination');
  }

  protected onDestinationEdit(): void {
    this.clearSuccessfulFeedback('destination');
  }

  protected onScheduleChange(frequency: Frequency): void {
    this.frequency.set(frequency);
    this.clearSuccessfulFeedback('schedule');
    void this.saveConfig('schedule');
  }

  /** Existing cadence/destination fields may save on blur, but every write uses
   * one serialized lane. Repeated blur events coalesce to the newest owned draft. */
  protected async saveConfig(origin: OperationalSaveOrigin = 'destination'): Promise<void> {
    if (!this.serverConfig()) return;
    const owner = this.captureSaveOwner();
    const payload = this.automaticPayload();
    const passwordGeneration = payload.smb_password === undefined ? null : this.passwordGeneration;

    if (this.pendingActivation && this.sameSaveOwner(this.pendingActivation.owner, owner)) {
      this.pendingActivation = {
        ...this.pendingActivation,
        payload: this.activationPayload(
          payload,
          this.pendingActivation.confirmedRetention ?? this.retentionPayload(),
        ),
        origins: this.mergeOrigins(this.pendingActivation.origins, [origin]),
        passwordGeneration,
      };
    } else {
      const previousOrigins =
        this.pendingAutomatic && this.sameSaveOwner(this.pendingAutomatic.owner, owner)
          ? this.pendingAutomatic.origins
          : [];
      this.pendingAutomatic = this.makeSaveIntent(
        'automatic',
        payload,
        this.mergeOrigins(previousOrigins, [origin]),
        null,
        passwordGeneration,
        owner,
      );
    }

    const blocked = this.blockedSave();
    if (
      blocked?.failureClass === 'validation' &&
      blocked.intent.kind === 'automatic' &&
      blocked.phase === 'ready' &&
      blocked.current
    ) {
      const blockedLoop = this.saveLoop;
      this.applyConfig(blocked.current, false);
      this.blockedSave.set(null);
      if (blockedLoop) await blockedLoop;
      await this.ensureSaveLoop();
    } else if (!blocked) {
      await this.ensureSaveLoop();
    }
  }

  protected async saveAndActivateRetention(): Promise<void> {
    if (this.retentionValidation() || this.inFlightSave()?.kind === 'activation') {
      return;
    }

    const owner = this.captureSaveOwner();
    const priorPending =
      this.pendingActivation && this.sameSaveOwner(this.pendingActivation.owner, owner)
        ? this.pendingActivation
        : null;
    if (priorPending && this.retentionMatches(priorPending.confirmedRetention)) {
      return;
    }
    const blocked = this.blockedSave();
    const blockedLoop = blocked ? this.saveLoop : null;
    if (blocked) {
      if (blocked.phase !== 'ready' || !blocked.current) {
        return;
      }
      if (
        blocked.intent.kind === 'activation' &&
        this.retentionMatches(blocked.intent.confirmedRetention)
      ) {
        return;
      }
      this.applyConfig(blocked.current, false);
      this.blockedSave.set(null);
    }

    const confirmedRetention = this.retentionPayload();
    const automatic = this.automaticPayload();
    const automaticOrigins =
      this.pendingAutomatic && this.sameSaveOwner(this.pendingAutomatic.owner, owner)
        ? this.pendingAutomatic.origins
        : [];
    const pendingOrigins = this.mergeOrigins(priorPending?.origins ?? [], automaticOrigins);
    this.pendingAutomatic = null;
    this.pendingActivation = this.makeSaveIntent(
      'activation',
      this.activationPayload(automatic, confirmedRetention),
      pendingOrigins,
      confirmedRetention,
      automatic.smb_password === undefined ? null : this.passwordGeneration,
      owner,
    );
    this.retentionSaveFeedback.set({
      owner,
      intentId: this.pendingActivation.id,
      level: 'info',
      message: this.inFlightSave()
        ? $localize`:Retention activation queue state|Activation waits for the current settings save:Activation queued. It will use the saved revision from the current request.`
        : $localize`:Retention activation progress|The confirmed policies are being saved:Saving and activating retention…`,
    });
    if (blockedLoop) {
      await blockedLoop;
    }
    await this.ensureSaveLoop();
  }

  private captureSaveOwner(): SaveOwner {
    return {
      sessionKey: this.sessionKey(),
      sessionGeneration: this.sessionGeneration,
      presentationGeneration: this.savePresentationGeneration,
    };
  }

  private ownsSaveOwner(owner: SaveOwner): boolean {
    return (
      !this.destroyed &&
      owner.sessionKey === this.sessionKey() &&
      owner.sessionGeneration === this.sessionGeneration &&
      owner.presentationGeneration === this.savePresentationGeneration
    );
  }

  private sameSaveOwner(left: SaveOwner, right: SaveOwner): boolean {
    return (
      left.sessionKey === right.sessionKey &&
      left.sessionGeneration === right.sessionGeneration &&
      left.presentationGeneration === right.presentationGeneration
    );
  }

  private makeSaveIntent(
    kind: SaveKind,
    payload: BackupConfigUpdateRequest,
    origins: OperationalSaveOrigin[],
    confirmedRetention: BackupConfigUpdateRequest | null,
    passwordGeneration: number | null,
    owner = this.captureSaveOwner(),
  ): SaveIntent {
    return {
      id: ++this.nextSaveIntentId,
      kind,
      owner,
      payload: this.clonePayload(payload),
      origins: [...origins],
      passwordGeneration,
      confirmedRetention: confirmedRetention ? this.clonePayload(confirmedRetention) : null,
    };
  }

  private clonePayload(payload: BackupConfigUpdateRequest): BackupConfigUpdateRequest {
    return {
      ...payload,
      local_retention: payload.local_retention ? { ...payload.local_retention } : undefined,
      offbox_retention: payload.offbox_retention ? { ...payload.offbox_retention } : undefined,
    };
  }

  private mergeOrigins(
    left: OperationalSaveOrigin[],
    right: OperationalSaveOrigin[],
  ): OperationalSaveOrigin[] {
    return [...new Set([...left, ...right])];
  }

  private async ensureSaveLoop(): Promise<void> {
    if (this.saveLoop) {
      const running = this.saveLoop;
      await running;
      if (
        !this.saveLoop &&
        !this.blockedSave() &&
        (this.pendingAutomatic || this.pendingActivation)
      ) {
        await this.ensureSaveLoop();
      }
      return;
    }
    const runGeneration = ++this.saveRunGeneration;
    const loop = this.runSaveLoop(runGeneration).finally(() => {
      if (this.saveLoop === loop) {
        this.saveLoop = null;
      }
    });
    this.saveLoop = loop;
    await loop;
  }

  private async runSaveLoop(runGeneration: number): Promise<void> {
    if (runGeneration !== this.saveRunGeneration) return;
    const feedbackGeneration = this.mutationRequestGeneration;
    let completedAny = false;
    this.configSaving.set(true);
    try {
      while (runGeneration === this.saveRunGeneration && !this.blockedSave()) {
        const intent = this.pendingAutomatic ?? this.pendingActivation;
        if (!intent) break;
        if (!this.ownsSaveOwner(intent.owner)) return;
        if (intent.kind === 'automatic') {
          this.pendingAutomatic = null;
        } else {
          this.pendingActivation = null;
          this.retentionSaveFeedback.set({
            owner: intent.owner,
            intentId: intent.id,
            level: 'info',
            message: $localize`:Retention activation progress|The confirmed policies are being saved:Saving and activating retention…`,
          });
        }
        this.inFlightSave.set(intent);
        const succeeded = await this.performSave(intent);
        if (!succeeded) return;
        completedAny = true;
      }
      if (completedAny && !this.blockedSave() && this.ownsActionFeedback(feedbackGeneration)) {
        void this.loadRecoveryStatus();
      }
    } finally {
      if (runGeneration === this.saveRunGeneration) {
        this.inFlightSave.set(null);
        this.configSaving.set(false);
      }
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

  private activationPayload(
    automatic: BackupConfigUpdateRequest,
    retention: BackupConfigUpdateRequest,
  ): BackupConfigUpdateRequest {
    return { ...this.clonePayload(automatic), ...this.clonePayload(retention) };
  }

  private async performSave(intent: SaveIntent): Promise<boolean> {
    const config = this.serverConfig();
    if (!config || !this.ownsSaveOwner(intent.owner)) {
      return false;
    }
    const payload = { ...this.clonePayload(intent.payload), expected_revision: config.revision };
    const { data, error, response } = await this.api.updateBackupConfig(payload);
    if (
      !this.ownsSaveOwner(intent.owner) ||
      this.inFlightSave()?.id !== intent.id ||
      this.inFlightSave()?.owner !== intent.owner
    ) {
      return false;
    }
    if (error || !data) {
      await this.blockFailedSave(intent, error, response?.status);
      return false;
    }

    // Adopt this response's committed opaque revision before draining another
    // intent. Later local edits remain in the signals and pending snapshots.
    this.applyConfig(data, false);
    const destinationStillMatches = this.destinationMatches(intent);
    if (
      intent.passwordGeneration != null &&
      intent.passwordGeneration === this.passwordGeneration &&
      intent.payload.smb_password === this.password()
    ) {
      this.password.set('');
      this.passwordEdited = false;
    }
    this.publishSaveSuccess(intent, destinationStillMatches);
    this.inFlightSave.set(null);
    return this.ownsSaveOwner(intent.owner);
  }

  private classifySaveFailure(status: number | undefined): SaveFailureClass {
    if (status === 401 || status === 403) return 'authentication';
    if (status === 409) return 'conflict';
    if (status === 422) return 'validation';
    if (status === 428) return 'precondition';
    return 'transport';
  }

  private async blockFailedSave(
    intent: SaveIntent,
    error: unknown,
    status: number | undefined,
  ): Promise<void> {
    const failureClass = this.classifySaveFailure(status);
    const message = apiErrorMessage(
      error,
      failureClass === 'conflict'
        ? $localize`:Configuration conflict|Another administrator or backup operation changed the settings:Backup settings changed or are busy. Your draft is preserved and was not replayed.`
        : failureClass === 'authentication'
          ? $localize`:Configuration authorization error|Authentication or backup-management permission must be restored:Backup settings were not saved. Sign in again or restore backup-management permission before continuing.`
          : failureClass === 'validation'
            ? $localize`:Configuration validation error|The server rejected the settings draft:Backup settings were rejected. Correct the highlighted values and confirm again.`
            : failureClass === 'precondition'
              ? $localize`:Configuration contract error|A revision-aware client unexpectedly received HTTP 428:The server did not accept the required revision. Reload current settings; this client will not fall back to timestamp or tokenless writes.`
              : $localize`:Configuration transport error|The settings response was not confirmed:Backup settings may have been accepted, but the response was not confirmed. Reload before deciding whether to retry.`,
    );
    const phase: ReconciliationPhase =
      failureClass === 'authentication'
        ? 'refreshFailed'
        : failureClass === 'validation'
          ? 'ready'
          : 'refreshing';
    this.blockedSave.set({
      intent,
      phase,
      failureClass,
      message,
      current: failureClass === 'validation' ? this.serverConfig() : null,
    });
    this.publishSaveFailure(intent, message);
    this.inFlightSave.set(null);
    if (
      failureClass === 'conflict' ||
      failureClass === 'transport' ||
      failureClass === 'precondition'
    ) {
      await this.reconcileBlockedSave(intent.id);
    }
  }

  private async reconcileBlockedSave(intentId: number): Promise<void> {
    const blocked = this.blockedSave();
    if (!blocked || blocked.intent.id !== intentId || !this.ownsSaveOwner(blocked.intent.owner)) {
      return;
    }
    const generation = ++this.conflictRequestGeneration;
    this.blockedSave.set({ ...blocked, phase: 'refreshing', current: null });
    const { data, error } = await this.api.getBackupConfig();
    const current = this.blockedSave();
    if (
      generation !== this.conflictRequestGeneration ||
      !current ||
      current.intent.id !== intentId ||
      !this.ownsSaveOwner(current.intent.owner)
    ) {
      return;
    }
    if (error || !data) {
      const reloadMessage = apiErrorMessage(
        error,
        $localize`:Configuration reload error|The current server settings could not be loaded:Current backup settings could not be reloaded. Your draft is still preserved.`,
      );
      this.blockedSave.set({ ...current, phase: 'refreshFailed', current: null });
      this.publishSaveFailure(current.intent, reloadMessage);
      return;
    }
    if (
      current.failureClass === 'transport' &&
      !(current.intent.kind === 'automatic' && this.pendingActivation) &&
      this.serverMatchesIntent(data, current.intent)
    ) {
      this.applyConfig(data, false);
      this.blockedSave.set(null);
      this.publishAmbiguousMatch(current.intent);
      return;
    }
    this.blockedSave.set({ ...current, phase: 'ready', current: data });
  }

  protected async reloadBlockedSave(): Promise<void> {
    const blocked = this.blockedSave();
    if (!blocked || blocked.phase === 'refreshing') return;
    await this.reconcileBlockedSave(blocked.intent.id);
  }

  protected useCurrentBlockedConfig(): void {
    const blocked = this.blockedSave();
    if (!blocked || blocked.phase !== 'ready' || !blocked.current) return;
    const current = blocked.current;
    this.invalidateConfigSaveState();
    this.applyConfig(current, true);
    void this.loadRecoveryStatus();
  }

  protected async retryBlockedDraft(): Promise<void> {
    const blocked = this.blockedSave();
    if (!this.canRetryBlockedSave() || !blocked?.current) return;

    this.applyConfig(blocked.current, false);
    const owner = blocked.intent.owner;
    const queuedActivation =
      this.pendingActivation && this.sameSaveOwner(this.pendingActivation.owner, owner)
        ? this.pendingActivation
        : null;
    const pendingOrigins =
      this.pendingAutomatic && this.sameSaveOwner(this.pendingAutomatic.owner, owner)
        ? this.pendingAutomatic.origins
        : [];
    let retry: SaveIntent;
    if (queuedActivation || blocked.intent.kind === 'activation') {
      const confirmedRetention =
        queuedActivation?.confirmedRetention ?? blocked.intent.confirmedRetention;
      if (!confirmedRetention || !this.retentionMatches(confirmedRetention)) return;
      const automatic = this.automaticPayload();
      retry = this.makeSaveIntent(
        'activation',
        this.activationPayload(automatic, confirmedRetention),
        this.mergeOrigins(
          this.mergeOrigins(blocked.intent.origins, queuedActivation?.origins ?? []),
          pendingOrigins,
        ),
        confirmedRetention,
        automatic.smb_password === undefined ? null : this.passwordGeneration,
        owner,
      );
      this.pendingActivation = retry;
      this.pendingAutomatic = null;
      this.retentionSaveFeedback.set({
        owner,
        intentId: retry.id,
        level: 'info',
        message: $localize`:Retention activation progress|The confirmed policies are being saved:Saving and activating retention…`,
      });
    } else {
      const automatic = this.automaticPayload();
      retry = this.makeSaveIntent(
        'automatic',
        automatic,
        this.mergeOrigins(blocked.intent.origins, pendingOrigins),
        null,
        automatic.smb_password === undefined ? null : this.passwordGeneration,
        owner,
      );
      this.pendingAutomatic = retry;
    }
    const blockedLoop = this.saveLoop;
    this.blockedSave.set(null);
    if (blockedLoop) {
      await blockedLoop;
    }
    await this.ensureSaveLoop();
  }

  private publishAmbiguousMatch(intent: SaveIntent): void {
    if (!this.ownsSaveOwner(intent.owner)) return;
    const feedback: ConfigSaveFeedback = {
      owner: intent.owner,
      intentId: intent.id,
      level: 'info',
      message: $localize`:Configuration ambiguous acknowledgement|The box matches the draft but the request outcome is unknown:The box now matches these settings, but this request’s response was not confirmed.`,
    };
    if (intent.origins.includes('schedule')) this.scheduleSaveFeedback.set(feedback);
    if (intent.origins.includes('destination')) this.destinationSaveFeedback.set(feedback);
    if (intent.kind === 'activation') this.retentionSaveFeedback.set(feedback);
  }

  private serverMatchesIntent(config: BackupConfig, intent: SaveIntent): boolean {
    const payload = intent.payload;
    if (payload.smb_password !== undefined) return false;
    const scalarFields = [
      'frequency',
      'smb_host',
      'smb_share',
      'smb_folder',
      'smb_username',
      'smb_domain',
      'local_max_bytes',
      'offbox_max_bytes',
      'local_min_free_bytes',
      'offbox_min_free_bytes',
    ] as const;
    for (const field of scalarFields) {
      if (payload[field] !== undefined && payload[field] !== config[field]) return false;
    }
    const policyMatches = (
      expected: BackupRetentionPolicyUpdate | undefined,
      actual: BackupRetentionPolicy,
    ) =>
      !expected ||
      (expected.mode === actual.mode &&
        expected.keep_all_days === actual.keep_all_days &&
        expected.daily_until_days === actual.daily_until_days &&
        expected.weekly_until_days === actual.weekly_until_days);
    return (
      policyMatches(payload.local_retention, config.local_retention) &&
      policyMatches(payload.offbox_retention, config.offbox_retention) &&
      (intent.kind !== 'activation' || !config.retention_review_required)
    );
  }

  private publishSaveFailure(intent: SaveIntent, message: string): void {
    if (!this.ownsSaveOwner(intent.owner)) return;
    const feedback: ConfigSaveFeedback = {
      owner: intent.owner,
      intentId: intent.id,
      level: 'error',
      message,
    };
    if (intent.origins.includes('schedule')) this.scheduleSaveFeedback.set(feedback);
    if (intent.origins.includes('destination')) this.destinationSaveFeedback.set(feedback);
    if (intent.kind === 'activation' || !!this.pendingActivation) {
      this.retentionSaveFeedback.set(feedback);
    }
  }

  private publishSaveSuccess(intent: SaveIntent, destinationStillMatches: boolean): void {
    if (!this.ownsSaveOwner(intent.owner)) return;
    if (intent.origins.includes('schedule') && this.frequency() === intent.payload.frequency) {
      this.scheduleSaveFeedback.set({
        owner: intent.owner,
        intentId: intent.id,
        level: 'success',
        message: $localize`:Schedule save success|The automatic backup schedule was saved:Schedule saved.`,
      });
    }
    if (intent.origins.includes('destination') && destinationStillMatches) {
      this.destinationSaveFeedback.set({
        owner: intent.owner,
        intentId: intent.id,
        level: 'success',
        message: $localize`:Destination save success|The Synology destination settings were saved:Destination saved.`,
      });
    }
    if (intent.kind === 'activation' && this.retentionMatches(intent.confirmedRetention)) {
      this.retentionSaveFeedback.set({
        owner: intent.owner,
        intentId: intent.id,
        level: 'success',
        message: $localize`:Retention activation success|The policies were saved and activation completed:Retention saved and activated. Pruning runs during independent maintenance.`,
      });
    }
  }

  private destinationMatches(intent: SaveIntent): boolean {
    const payload = intent.payload;
    const passwordMatches =
      payload.smb_password === undefined ||
      (intent.passwordGeneration === this.passwordGeneration &&
        payload.smb_password === this.password());
    return (
      (this.host() || null) === payload.smb_host &&
      (this.share() || null) === payload.smb_share &&
      (this.folder() || null) === payload.smb_folder &&
      (this.username() || null) === payload.smb_username &&
      (this.domain() || null) === payload.smb_domain &&
      passwordMatches
    );
  }

  private clearSuccessfulFeedback(origin: OperationalSaveOrigin): void {
    const target = origin === 'schedule' ? this.scheduleSaveFeedback : this.destinationSaveFeedback;
    if (target()?.level !== 'error') target.set(null);
  }

  protected retentionMatches(retention: BackupConfigUpdateRequest | null): boolean {
    return (
      !!retention &&
      this.retentionFingerprint(retention) === this.retentionFingerprint(this.retentionPayload())
    );
  }

  private retentionFingerprint(payload: BackupConfigUpdateRequest): string {
    return JSON.stringify({
      local_retention: payload.local_retention,
      offbox_retention: payload.offbox_retention,
      local_max_bytes: payload.local_max_bytes,
      offbox_max_bytes: payload.offbox_max_bytes,
      local_min_free_bytes: payload.local_min_free_bytes,
      offbox_min_free_bytes: payload.offbox_min_free_bytes,
      confirm_retention_policy: payload.confirm_retention_policy,
    });
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
      // Restore rotates destination generations/revision and re-enables review.
      // Restore is an ownership boundary, so adopt its authoritative settings.
      this.applyConfig(data, true);
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
    if (this.retentionSaveFeedback()?.level !== 'error') {
      this.retentionSaveFeedback.set(null);
    }
  }

  protected activationActionDisabled(): boolean {
    if (!this.serverConfig() || !!this.retentionValidation() || !this.retentionChanged()) {
      return true;
    }
    if (this.inFlightSave()?.kind === 'activation') {
      return true;
    }
    const pending = this.pendingActivation;
    if (pending && this.retentionMatches(pending.confirmedRetention)) {
      return true;
    }
    const blocked = this.blockedSave();
    if (!blocked) return false;
    if (blocked.phase !== 'ready' || !blocked.current) return true;
    return (
      blocked.intent.kind === 'activation' &&
      this.retentionMatches(blocked.intent.confirmedRetention)
    );
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

  protected isReviewOnlyDestination(status: BackupDestinationRecoveryStatus): boolean {
    const allowed = new Set(['retention_review_required', 'coverage_unknown']);
    return (
      status.status === 'degraded' &&
      status.reason_codes.includes('retention_review_required') &&
      status.reason_codes.every((reason) => allowed.has(reason))
    );
  }

  protected isOverallReviewOnly(status: BackupRecoveryStatus): boolean {
    if (status.overall_status !== 'degraded') return false;
    const configured = [status.local, status.offbox].filter(
      (destination) => destination.configured,
    );
    return (
      configured.some((destination) => this.isReviewOnlyDestination(destination)) &&
      configured.every(
        (destination) =>
          destination.status === 'healthy' || this.isReviewOnlyDestination(destination),
      )
    );
  }

  protected recoveryStateLabel(status: BackupDestinationRecoveryStatus): string {
    return this.isReviewOnlyDestination(status)
      ? $localize`:Recovery review state|Only administrator retention review degrades this destination:Review required`
      : this.destinationStateLabel(status.status);
  }

  protected overallRecoveryStateLabel(status: BackupRecoveryStatus): string {
    return this.isOverallReviewOnly(status)
      ? $localize`:Recovery review state|Only administrator retention review degrades configured destinations:Review required`
      : this.destinationStateLabel(status.overall_status);
  }

  protected recoveryStateAriaLabel(status: BackupDestinationRecoveryStatus): string {
    return this.isReviewOnlyDestination(status)
      ? $localize`:Recovery review accessibility|Names the presentation and underlying API state:Review required. API recovery status is degraded because retention policy review is pending.`
      : $localize`:Recovery status accessibility|Names the API-derived destination state:API recovery status is ${this.destinationStateLabel(status.status)}:status:.`;
  }

  protected destinationStateDescription(status: BackupDestinationRecoveryStatus): string {
    if (this.isReviewOnlyDestination(status)) {
      return $localize`:Recovery review detail|Review is the only reported degradation reason:Automatic pruning is paused until an administrator activates retention. The server reports no additional inventory, read-probe, capacity, or anomaly reason.`;
    }
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
