import { Component, computed, inject, OnInit, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

type Frequency = 'daily' | 'weekly' | 'off';

@Component({
  selector: 'app-backups',
  templateUrl: './backups.html',
  styleUrl: './backups.scss',
  imports: [FormsModule],
})
export class Backups implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly isOwner = computed(() => this.auth.role() === 'owner');

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

  protected readonly latest = signal<{ status: string; completed_at?: string | null; size_bytes?: number | null; error_message?: string | null; remote_status?: string | null; remote_error?: string | null } | null>(null);
  protected readonly remoteBackups = signal<{ filename: string; size_bytes: number; modified_at: number }[]>([]);

  protected readonly busy = signal(false);
  protected readonly checking = signal(false);
  protected readonly checkResult = signal<{ writable: boolean; reason?: string | null } | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly statusMessage = signal<string | null>(null);

  protected readonly backups = resource({
    loader: async () => {
      const { data, error } = await this.api.listBackups();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load backups.'));
      }
      return data.backups;
    },
  });

  async ngOnInit(): Promise<void> {
    if (this.isOwner()) {
      await this.loadConfig();
    }
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
      this.actionError.set(apiErrorMessage(error, 'Failed to save settings.'));
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
      this.actionError.set(apiErrorMessage(error, 'Failed to test connection.'));
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
      this.actionError.set(apiErrorMessage(error, 'Failed to create backup.'));
      return;
    }
    if (data) this.latest.set(data);
    this.statusMessage.set('Backup complete.');
    this.backups.reload();
    await this.loadRemote();
  }

  protected async restore(id: string): Promise<void> {
    if (this.busy()) return;
    if (!confirm('Restore this backup? This REPLACES all current data with the backup contents. This cannot be undone.')) return;
    this.busy.set(true);
    this.actionError.set(null);
    const { error } = await this.api.restoreBackup(id);
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to restore backup.'));
      return;
    }
    this.backups.reload();
  }

  protected async restoreRemote(filename: string): Promise<void> {
    if (this.busy()) return;
    if (!confirm(`Restore from ${filename}? This REPLACES all current data with the backup contents. This cannot be undone.`)) return;
    this.busy.set(true);
    this.actionError.set(null);
    const { error } = await this.api.restoreRemoteBackup(filename);
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to restore from Synology.'));
      return;
    }
    this.statusMessage.set(`Restored from ${filename}.`);
  }

  protected async revealKey(): Promise<void> {
    const { data, error } = await this.api.getBackupEncryptionKey();
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to load key.'));
      return;
    }
    this.revealedKey.set(data?.key ?? '(not configured)');
  }

  protected async copyKey(): Promise<void> {
    const key = this.revealedKey();
    if (key) {
      await navigator.clipboard?.writeText(key);
      this.statusMessage.set('Encryption key copied.');
    }
  }

  protected async deleteLocal(id: string): Promise<void> {
    if (!confirm('Delete this on-box backup?')) return;
    const { error } = await this.api.deleteBackup(id);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete backup.'));
      return;
    }
    this.backups.reload();
  }

  protected async deleteRemote(filename: string): Promise<void> {
    if (!confirm(`Delete ${filename} from the Synology?`)) return;
    const { error } = await this.api.deleteRemoteBackup(filename);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete from Synology.'));
      return;
    }
    this.remoteBackups.set((await this.api.listRemoteBackups()).data?.backups ?? []);
  }

  protected formatDate(epoch: number): string {
    return new Date(epoch * 1000).toLocaleString();
  }
}
