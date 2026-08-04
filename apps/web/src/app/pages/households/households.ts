import { DatePipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-households',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
  ],
  templateUrl: './households.html',
  styleUrl: './households.scss',
})
export class Households {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  // #180: the whole page is operator territory — the server 403s non-admins.
  protected readonly canHost = () => this.auth.hasRight('system.admin');

  protected readonly households = resource({
    loader: async () => {
      if (!this.canHost()) {
        return [];
      }
      const { data, error } = await this.api.listHostedHouseholds();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load households.'));
      }
      return data.households;
    },
  });

  protected readonly createForm = this.formBuilder.nonNullable.group({
    displayName: ['', Validators.required],
    baseCurrency: ['USD', [Validators.required, Validators.pattern(/^[A-Za-z]{3}$/)]],
    ownerEmail: ['', [Validators.required, Validators.email]],
  });

  protected readonly creating = signal(false);
  protected readonly createError = signal<string | null>(null);
  /** The freshly-minted one-time join link, shown until dismissed. */
  protected readonly inviteLink = signal<{ email: string; url: string; expiresAt: string } | null>(
    null,
  );
  protected readonly inviteLinkCopied = signal(false);

  private joinUrl(token: string): string {
    // Fragment (not query) so the secret never reaches server access logs.
    return `${window.location.origin}/join#token=${token}`;
  }

  protected async createHousehold(): Promise<void> {
    if (this.createForm.invalid || this.creating()) {
      this.createForm.markAllAsTouched();
      return;
    }
    this.creating.set(true);
    this.createError.set(null);
    const { displayName, baseCurrency, ownerEmail } = this.createForm.getRawValue();
    const { data, error } = await this.api.createHostedHousehold({
      display_name: displayName,
      base_currency: baseCurrency.toUpperCase(),
      owner_email: ownerEmail,
    });
    this.creating.set(false);
    if (error || !data) {
      this.createError.set(apiErrorMessage(error, 'Failed to create the household.'));
      return;
    }
    this.createForm.reset({ displayName: '', baseCurrency: 'USD', ownerEmail: '' });
    this.inviteLink.set({
      email: ownerEmail,
      url: this.joinUrl(data.invite_token),
      expiresAt: data.invite_expires_at,
    });
    this.inviteLinkCopied.set(false);
    this.households.reload();
  }

  protected async copyInviteLink(): Promise<void> {
    const link = this.inviteLink();
    if (!link) {
      return;
    }
    await navigator.clipboard.writeText(link.url);
    this.inviteLinkCopied.set(true);
  }

  protected dismissInviteLink(): void {
    this.inviteLink.set(null);
  }
}
