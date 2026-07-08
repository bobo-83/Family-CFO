import { Component, computed, inject, resource, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-backups',
  templateUrl: './backups.html',
  styleUrl: './backups.scss',
})
export class Backups {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly isOwner = computed(() => this.auth.role() === 'owner');

  protected readonly backups = resource({
    loader: async () => {
      const { data, error } = await this.api.listBackups();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load backups.'));
      }
      return data.backups;
    },
  });

  protected readonly busy = signal(false);
  protected readonly actionError = signal<string | null>(null);

  protected async createBackup(): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.actionError.set(null);
    const { error } = await this.api.createBackup();
    this.busy.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to create backup.'));
      return;
    }
    this.backups.reload();
  }

  protected async restore(id: string): Promise<void> {
    if (this.busy()) {
      return;
    }
    if (
      !confirm(
        'Restore this backup? This REPLACES all current data (accounts, transactions, documents) with the backup contents. This cannot be undone.',
      )
    ) {
      return;
    }
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
}
