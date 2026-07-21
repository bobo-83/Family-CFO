import { Component, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';
import type { InvitePreview } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { setAuthState } from '../../core/token-store';
import { apiErrorMessage } from '../../shared/api-error';

/**
 * ADR 0056: the invite landing page. The admin shared a one-time link whose
 * token rides in the URL FRAGMENT (never a query param, so it stays out of
 * server access logs). The invitee sees who invited them where, chooses their
 * own display name + password, and lands signed-in on the dashboard.
 */
@Component({
  selector: 'app-join',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
  ],
  templateUrl: './join.html',
  styleUrl: './join.scss',
})
export class Join {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly formBuilder = inject(FormBuilder);

  private token = '';
  protected readonly preview = signal<InvitePreview | null>(null);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  protected readonly form = this.formBuilder.nonNullable.group({
    displayName: ['', Validators.required],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  constructor() {
    this.token = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token') ?? '';
    void this.load();
  }

  private async load(): Promise<void> {
    if (!this.token) {
      this.loading.set(false);
      this.loadError.set('This invite link is incomplete — ask for a new one.');
      return;
    }
    const { data, error } = await this.api.previewInvite(this.token);
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(
        apiErrorMessage(error, 'This invite link is invalid or expired — ask for a new one.'),
      );
      return;
    }
    this.preview.set(data);
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.submitError.set(null);
    const { displayName, password } = this.form.getRawValue();
    const { data, error } = await this.api.acceptInvite({
      token: this.token,
      password,
      display_name: displayName,
    });
    this.submitting.set(false);
    if (error || !data) {
      this.submitError.set(
        apiErrorMessage(error, 'Could not join — the link may have expired. Ask for a new one.'),
      );
      return;
    }
    setAuthState({
      accessToken: data.access_token,
      householdId: data.household_id,
      userId: data.user_id,
      role: data.role,
      roleName: data.role_name ?? undefined,
      rights: data.rights ?? undefined,
    });
    await this.router.navigateByUrl('/overview');
  }
}
