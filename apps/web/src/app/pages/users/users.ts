import { DatePipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type { Invite, PairedDevice } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-users',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './users.html',
  styleUrl: './users.scss',
})
export class Users {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  // ADR 0034: sections gate by RIGHT.
  protected readonly currentUserId = () => this.auth.userId();
  protected readonly canRevokeDevices = () => this.auth.hasRight('devices.manage');
  protected readonly canManageMembers = () => this.auth.hasRight('members.manage');

  /** The household's roles (presets + custom) for the assignment pickers. */
  protected readonly roles = resource({
    loader: async () => {
      if (!this.canManageMembers()) {
        return [];
      }
      const { data, error } = await this.api.listRoles();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load roles.`));
      }
      return data.roles;
    },
  });

  protected readonly members = resource({
    loader: async () => {
      const { data, error } = await this.api.listMembers();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load members.`));
      }
      return data.members;
    },
  });

  protected readonly memberForm = this.formBuilder.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(8)]],
    displayName: ['', Validators.required],
    roleId: ['', Validators.required],
  });

  protected readonly memberSubmitting = signal(false);
  protected readonly memberError = signal<string | null>(null);

  protected async addMember(): Promise<void> {
    if (this.memberForm.invalid || this.memberSubmitting()) {
      this.memberForm.markAllAsTouched();
      return;
    }
    this.memberSubmitting.set(true);
    this.memberError.set(null);
    const { email, password, displayName, roleId } = this.memberForm.getRawValue();
    const { error } = await this.api.createMember({
      email,
      password,
      display_name: displayName,
      role_id: roleId,
    });
    this.memberSubmitting.set(false);
    if (error) {
      this.memberError.set(apiErrorMessage(error, $localize`Failed to add member.`));
      return;
    }
    this.memberForm.reset({ email: '', password: '', displayName: '', roleId: '' });
    this.members.reload();
  }

  // --- System administrators (ADR 0065): box-global roster — controls model
  // swaps for EVERY household. Visible only to current system admins; a fresh
  // grant takes effect at the grantee's next sign-in.
  protected readonly canManageSystemAdmins = () => this.auth.hasRight('system.admin');

  protected readonly systemAdmins = resource({
    loader: async () => {
      if (!this.canManageSystemAdmins()) {
        return [];
      }
      const { data, error } = await this.api.listSystemAdmins();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load system administrators.`));
      }
      return data.admins;
    },
  });

  // The tooltip on a disabled Revoke button. It rides an interpolated [title]
  // binding, so it is localized here rather than with i18n-title (which Angular
  // would silently drop on a bound attribute).
  protected readonly lastSystemAdminHint = $localize`:Tooltip|Why the last system administrator cannot be revoked:The box must keep at least one system administrator`;

  protected readonly sysAdminForm = this.formBuilder.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
  });
  protected readonly sysAdminSubmitting = signal(false);
  protected readonly sysAdminError = signal<string | null>(null);

  protected async grantSystemAdmin(): Promise<void> {
    if (this.sysAdminForm.invalid || this.sysAdminSubmitting()) {
      this.sysAdminForm.markAllAsTouched();
      return;
    }
    this.sysAdminSubmitting.set(true);
    this.sysAdminError.set(null);
    const { error } = await this.api.grantSystemAdmin(this.sysAdminForm.getRawValue().email.trim());
    this.sysAdminSubmitting.set(false);
    if (error) {
      this.sysAdminError.set(
        apiErrorMessage(error, $localize`Could not grant system administrator.`),
      );
      return;
    }
    this.sysAdminForm.reset({ email: '' });
    this.systemAdmins.reload();
  }

  protected async revokeSystemAdmin(userId: string): Promise<void> {
    this.sysAdminError.set(null);
    const { error } = await this.api.revokeSystemAdmin(userId);
    if (error) {
      this.sysAdminError.set(apiErrorMessage(error, $localize`Could not revoke.`));
      return;
    }
    this.systemAdmins.reload();
  }

  // --- Invitations (ADR 0056): copy-link onboarding. The box sends no email —
  // the admin copies the link and shares it themselves. The link is shown only
  // at creation/regeneration (the token is stored hashed).
  protected readonly invites = resource({
    loader: async () => {
      if (!this.canManageMembers()) {
        return [];
      }
      const { data, error } = await this.api.listInvites();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load invitations.`));
      }
      return data.invites;
    },
  });

  protected readonly inviteForm = this.formBuilder.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    roleId: ['', Validators.required],
  });

  protected readonly inviteSubmitting = signal(false);
  protected readonly inviteError = signal<string | null>(null);
  /** The freshly-minted one-time link, shown until dismissed. */
  protected readonly inviteLink = signal<{ email: string; url: string } | null>(null);
  protected readonly inviteLinkCopied = signal(false);

  private joinUrl(token: string): string {
    // Fragment (not query) so the secret never reaches server access logs.
    return `${window.location.origin}/join#token=${token}`;
  }

  protected async sendInvite(): Promise<void> {
    if (this.inviteForm.invalid || this.inviteSubmitting()) {
      this.inviteForm.markAllAsTouched();
      return;
    }
    this.inviteSubmitting.set(true);
    this.inviteError.set(null);
    const { email, roleId } = this.inviteForm.getRawValue();
    const { data, error } = await this.api.createInvite({ email, role_id: roleId });
    this.inviteSubmitting.set(false);
    if (error || !data) {
      this.inviteError.set(apiErrorMessage(error, $localize`Failed to create the invite.`));
      return;
    }
    this.inviteForm.reset({ email: '', roleId: '' });
    this.inviteLink.set({ email: data.invite.email, url: this.joinUrl(data.invite_token) });
    this.inviteLinkCopied.set(false);
    this.invites.reload();
  }

  protected async regenerateInvite(invite: Invite): Promise<void> {
    const { data, error } = await this.api.regenerateInviteToken(invite.id);
    if (error || !data) {
      this.inviteError.set(apiErrorMessage(error, $localize`Failed to mint a new link.`));
      return;
    }
    this.inviteLink.set({ email: data.invite.email, url: this.joinUrl(data.invite_token) });
    this.inviteLinkCopied.set(false);
    this.invites.reload();
  }

  protected async revokeInvite(invite: Invite): Promise<void> {
    const { error } = await this.api.revokeInvite(invite.id);
    if (error) {
      this.inviteError.set(apiErrorMessage(error, $localize`Failed to revoke the invite.`));
      return;
    }
    this.invites.reload();
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

  protected async changeRole(userId: string, roleId: string): Promise<void> {
    const { error } = await this.api.updateMemberRole(userId, { role_id: roleId });
    if (error) {
      this.memberError.set(apiErrorMessage(error, $localize`Failed to change role.`));
      return;
    }
    this.members.reload();
  }

  protected async removeMember(userId: string): Promise<void> {
    if (!confirm(
        $localize`:Confirmation|Browser confirm shown before a household member is removed:Remove this member? Their active sessions will be revoked.`,
      )) {
      return;
    }
    const { error } = await this.api.deleteMember(userId);
    if (error) {
      this.memberError.set(apiErrorMessage(error, $localize`Failed to remove member.`));
      return;
    }
    this.members.reload();
  }

  protected readonly devices = resource({
    loader: async () => {
      const { data, error } = await this.api.listPairedDevices();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load paired devices.`));
      }
      return data.devices;
    },
  });

  /** Devices paired to a specific member. */
  protected devicesForUser(userId: string): PairedDevice[] {
    return (this.devices.value() ?? []).filter((device) => device.user_id === userId);
  }

  /** Devices not linked to any current member (legacy pairings, removed members). */
  protected unassignedDevices(): PairedDevice[] {
    const memberIds = new Set((this.members.value() ?? []).map((member) => member.user_id));
    return (this.devices.value() ?? []).filter(
      (device) => !device.user_id || !memberIds.has(device.user_id),
    );
  }

  // Each member row expands to reveal that member's revocable devices.
  protected readonly expandedUserId = signal<string | null>(this.auth.userId());

  protected toggleExpanded(userId: string): void {
    this.expandedUserId.update((current) => (current === userId ? null : userId));
  }

  protected isExpanded(userId: string): boolean {
    return this.expandedUserId() === userId;
  }

  protected readonly revokingId = signal<string | null>(null);
  protected readonly revokeError = signal<string | null>(null);

  protected async revokeDevice(deviceId: string): Promise<void> {
    if (this.revokingId()) {
      return;
    }

    this.revokingId.set(deviceId);
    this.revokeError.set(null);

    const { error } = await this.api.revokePairedDevice(deviceId);

    this.revokingId.set(null);

    if (error) {
      this.revokeError.set(apiErrorMessage(error, $localize`Failed to revoke device.`));
      return;
    }

    this.devices.reload();
  }
}
