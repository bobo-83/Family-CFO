import { DatePipe } from '@angular/common';
import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { ImportSourceType } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-imports',
  imports: [ReactiveFormsModule, DatePipe],
  templateUrl: './imports.html',
  styleUrl: './imports.scss',
})
export class Imports {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly canReview = computed(() => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  });

  protected readonly accounts = resource({
    loader: async () => {
      const { data, error } = await this.api.listAccounts();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load accounts.'));
      }
      return data.accounts;
    },
  });

  protected readonly imports = resource({
    loader: async () => {
      const { data, error } = await this.api.listImports();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load imports.'));
      }
      return data.imports;
    },
  });

  protected readonly form = this.formBuilder.nonNullable.group({
    sourceType: ['csv' as ImportSourceType, Validators.required],
    accountId: ['', Validators.required],
  });

  protected selectedFile: File | null = null;
  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  protected onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile = input.files?.[0] ?? null;
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    if (!this.selectedFile) {
      this.submitError.set('Choose a file to import.');
      return;
    }

    this.submitting.set(true);
    this.submitError.set(null);
    const { sourceType, accountId } = this.form.getRawValue();
    const created = await this.api.createImport({
      source_type: sourceType,
      filename: this.selectedFile.name,
      account_id: accountId,
    });
    if (created.error || !created.data) {
      this.submitting.set(false);
      this.submitError.set(apiErrorMessage(created.error, 'Failed to register import.'));
      return;
    }

    const uploaded = await this.api.uploadImportFile(created.data.id, this.selectedFile);
    this.submitting.set(false);
    if (uploaded.error) {
      this.submitError.set(apiErrorMessage(uploaded.error, 'Failed to upload file.'));
      return;
    }
    this.selectedFile = null;
    this.imports.reload();
  }

  protected async apply(id: string): Promise<void> {
    const { error } = await this.api.applyImport(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to apply import.'));
      return;
    }
    this.imports.reload();
  }

  protected async discard(id: string): Promise<void> {
    if (!confirm('Discard this import and delete its pending transactions?')) {
      return;
    }
    const { error } = await this.api.discardImport(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to discard import.'));
      return;
    }
    this.imports.reload();
  }

  // --- Linked institutions (M27, ADR 0015) ----------------------------------

  protected readonly connections = resource({
    loader: async () => {
      const { data, error } = await this.api.listConnections();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load linked institutions.'));
      }
      return data.connections;
    },
  });

  protected readonly connectionForm = this.formBuilder.nonNullable.group({
    displayName: ['', Validators.required],
    setupToken: ['', [Validators.required, Validators.minLength(8)]],
  });

  protected readonly linking = signal(false);
  protected readonly linkError = signal<string | null>(null);
  protected readonly syncingId = signal<string | null>(null);
  protected readonly syncMessage = signal<string | null>(null);

  protected async linkInstitution(): Promise<void> {
    if (this.connectionForm.invalid || this.linking()) {
      this.connectionForm.markAllAsTouched();
      return;
    }
    this.linking.set(true);
    this.linkError.set(null);
    const { displayName, setupToken } = this.connectionForm.getRawValue();
    const { error } = await this.api.createConnection({
      provider: 'simplefin',
      display_name: displayName,
      setup_token: setupToken.trim(),
    });
    this.linking.set(false);
    if (error) {
      this.linkError.set(apiErrorMessage(error, 'Could not link the institution.'));
      return;
    }
    this.connectionForm.reset({ displayName: '', setupToken: '' });
    this.syncMessage.set('Linked! The first sync started automatically — accounts appear shortly.');
    this.connections.reload();
    // The initial background sync usually lands within seconds; refresh once.
    setTimeout(() => {
      this.connections.reload();
      this.imports.reload();
    }, 8000);
  }

  protected async syncNow(connectionId: string): Promise<void> {
    if (this.syncingId()) {
      return;
    }
    this.syncingId.set(connectionId);
    this.syncMessage.set(null);
    const { data, error } = await this.api.syncConnection(connectionId);
    this.syncingId.set(null);
    if (error || !data) {
      this.syncMessage.set(apiErrorMessage(error, 'Sync failed.'));
    } else {
      this.syncMessage.set(
        `Synced ${data.accounts_synced} account(s): ${data.imported} new, ` +
          `${data.duplicates_skipped} duplicate(s) skipped.`,
      );
    }
    this.connections.reload();
    this.imports.reload();
  }

  protected async unlink(connectionId: string): Promise<void> {
    if (!window.confirm('Unlink this institution? Imported transactions are kept.')) {
      return;
    }
    const { error } = await this.api.deleteConnection(connectionId);
    if (error) {
      this.syncMessage.set(apiErrorMessage(error, 'Failed to unlink.'));
      return;
    }
    this.connections.reload();
  }
}

