import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, OnInit, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type { HouseholdKeyStatus, RemoteBackup } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

type Frequency = 'daily' | 'weekly' | 'off';

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

  protected readonly isOwner = computed(() => this.auth.hasRight('backups.manage'));

  // Synology SMB settings (auto-saved as they change).
  protected readonly host = signal('');
  protected readonly share = signal('');
  protected readonly folder = signal('');
  protected readonly username = signal('');
  protected readonly password = signal('');
  protected readonly domain = signal('');
  protected readonly frequency = signal<Frequency>('daily');
  protected readonly maxGB = signal<number>(0);
  protected readonly hasStoredPassword = signal(false);
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

  protected readonly latest = signal<{ status: string; completed_at?: string | null; size_bytes?: number | null; error_message?: string | null; remote_status?: string | null; remote_error?: string | null } | null>(null);
  protected readonly remoteBackups = signal<RemoteBackup[]>([]);

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
  protected readonly checkResult = signal<{ writable: boolean; reason?: string | null } | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly statusMessage = signal<string | null>(null);

  protected readonly backups = resource({
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

  async ngOnInit(): Promise<void> {
    this.loadRunningVersion();
    if (this.isOwner()) {
      await Promise.all([this.loadConfig(), this.loadKeyStatus()]);
    }
  }

  /** The box's running version (same unauthenticated /health the shell footer
   * uses) — flags backups the server would refuse to restore with a 409. */
  protected readonly runningVersion = signal<string | null>(null);

  private loadRunningVersion(): void {
    void fetch('/api/v1/health')
      .then((response) => response.json())
      .then((health: { version?: string }) => this.runningVersion.set(health.version ?? null))
      .catch(() => this.runningVersion.set(null));
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

  private async loadConfig(): Promise<void> {
    const { data, error } = await this.api.getBackupConfig();
    if (error || !data) {
      return;
    }
    this.host.set(data.smb_host ?? '');
    this.share.set(data.smb_share ?? '');
    this.folder.set(data.smb_folder ?? '');
    this.username.set(data.smb_username ?? '');
    this.domain.set(data.smb_domain ?? '');
    this.hasStoredPassword.set(data.has_password ?? false);
    this.frequency.set((data.frequency as Frequency) ?? 'daily');
    this.maxGB.set(data.max_bytes ? data.max_bytes / 1_000_000_000 : 0);
    this.password.set('');
    this.passwordEdited = false;
    this.latest.set(data.latest ?? null);
    if (data.smb_host) {
      await this.loadRemote();
    }
  }

  protected onPasswordInput(): void {
    this.passwordEdited = true;
  }

  protected async saveConfig(): Promise<void> {
    const { data, error } = await this.api.updateBackupConfig({
      frequency: this.frequency(),
      smb_host: this.host() || undefined,
      smb_share: this.share() || undefined,
      smb_folder: this.folder() || undefined,
      smb_username: this.username() || undefined,
      smb_password: this.passwordEdited ? this.password() : undefined,
      smb_domain: this.domain() || undefined,
      max_bytes: this.maxGB() > 0 ? Math.round(this.maxGB() * 1_000_000_000) : undefined,
    });
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to save settings.`));
      return;
    }
    this.actionError.set(null);
    if (data) {
      this.hasStoredPassword.set(data.has_password ?? false);
      this.latest.set(data.latest ?? null);
    }
    if (this.passwordEdited) {
      this.password.set('');
      this.passwordEdited = false;
    }
  }

  protected canTest = computed(() => !!this.host() && !!this.share() && !!this.username() && (this.passwordEdited || this.hasStoredPassword()));

  protected async testConnection(): Promise<void> {
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
    this.checking.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to test connection.`));
      return;
    }
    this.checkResult.set(data ?? null);
  }

  private async loadRemote(): Promise<void> {
    const { data } = await this.api.listRemoteBackups();
    this.remoteBackups.set(data?.backups ?? []);
  }

  protected async createBackup(): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.createBackup();
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to create backup.`));
      return;
    }
    if (data) this.latest.set(data);
    this.statusMessage.set($localize`:Status message|A backup finished successfully:Backup complete.`);
    this.backups.reload();
    await this.loadRemote();
  }

  protected async restore(id: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an on-box backup is restored over the live data:Restore this backup? This REPLACES all current data with the backup contents. This cannot be undone.`,
      )
    )
      return;
    this.busy.set(true);
    this.actionError.set(null);
    const { error } = await this.api.restoreBackup(id);
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to restore backup.`));
      return;
    }
    this.backups.reload();
  }

  protected async restoreRemote(filename: string): Promise<void> {
    if (this.busy()) return;
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an off-box snapshot is restored over the live data:Restore from ${filename}:filename:? This REPLACES all current data with the backup contents. This cannot be undone.`,
      )
    )
      return;
    this.busy.set(true);
    this.actionError.set(null);
    const { error } = await this.api.restoreRemoteBackup(filename);
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to restore from Synology.`));
      return;
    }
    this.statusMessage.set(
      $localize`:Status message|An off-box snapshot was restored:Restored from ${filename}:filename:.`,
    );
  }

  protected async revealKey(): Promise<void> {
    const { data, error } = await this.api.getBackupEncryptionKey();
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to load key.`));
      return;
    }
    this.revealedKey.set(
      data?.key ?? $localize`:Backup key value|Shown in place of the key when the box has none:(not configured)`,
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
    const { data } = await this.api.getHouseholdKeyStatus();
    this.keyStatus.set(data ?? null);
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
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.generateRecoveryKey();
    this.busy.set(false);
    if (error) {
      // 409 (encryption off) carries a human message — show it verbatim.
      this.actionError.set(apiErrorMessage(error, $localize`Failed to create recovery key.`));
      return;
    }
    this.generatedRecoveryKey.set(data?.recovery_key ?? null);
    await this.loadKeyStatus();
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
        ? $localize`:Confirmation|Browser confirm before the household is sealed:Seal this household? After a restart, nothing is readable until someone signs in. Unattended sync, snapshots and study stop for as long as you stay sealed — signing in does not resume them. Encrypted backups still run.`
        : $localize`:Confirmation|Browser confirm before the household leaves sealed mode:Switch back to convenient? The box keeps a spare of your data key again, so overnight work runs without anyone signed in.`;
    if (!confirm(consequence)) return;
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.setSealMode(mode);
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
    this.busy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.unlockWithRecoveryKey(key);
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
    this.exporting.set(true);
    this.exportError.set(null);
    try {
      const { blob, filename } = await this.api.downloadHouseholdExport();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      this.exportError.set(
        error instanceof Error
          ? error.message
          : $localize`:Error message|The household export could not be produced:Export failed.`,
      );
    } finally {
      this.exporting.set(false);
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

  protected async deleteLocal(id: string): Promise<void> {
    if (!confirm($localize`:Confirmation|Browser confirm before an on-box backup is deleted:Delete this on-box backup?`))
      return;
    const { error } = await this.api.deleteBackup(id);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to delete backup.`));
      return;
    }
    this.backups.reload();
  }

  protected async deleteRemote(filename: string): Promise<void> {
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an off-box snapshot is deleted:Delete ${filename}:filename: from the Synology?`,
      )
    )
      return;
    const { error } = await this.api.deleteRemoteBackup(filename);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to delete from Synology.`));
      return;
    }
    this.remoteBackups.set((await this.api.listRemoteBackups()).data?.backups ?? []);
  }

  protected formatDate(epoch: number): string {
    return new Date(epoch * 1000).toLocaleString();
  }
}
