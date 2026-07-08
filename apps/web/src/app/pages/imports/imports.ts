import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { ImportSourceType } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-imports',
  imports: [ReactiveFormsModule],
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
}
