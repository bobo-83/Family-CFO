import { DatePipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import QRCode from 'qrcode';
import type { Invite, Member, PairedDevice } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

interface PairingSessionView {
  /** The member this QR pairs a device for. */
  userId: string;
  qrPayload: string;
  qrImageDataUrl: string;
  expiresAt: string;
}

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

  // ADR 0034: sections gate by RIGHT. Pairing your OWN device is always allowed;
  // minting a code for another member needs device management.
  protected readonly currentUserId = () => this.auth.userId();
  protected readonly canRevokeDevices = () => this.auth.hasRight('devices.manage');
  protected readonly canManageDevices = () => this.auth.hasRight('devices.manage');
  protected readonly canManageMembers = () => this.auth.hasRight('members.manage');

  /** The household's roles (presets + custom) for the assignment pickers. */
  protected readonly roles = resource({
    loader: async () => {
      if (!this.canManageMembers()) {
        return [];
      }
      const { data, error } = await this.api.listRoles();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load roles.'));
      }
      return data.roles;
    },
  });

  protected readonly members = resource({
    loader: async () => {
      const { data, error } = await this.api.listMembers();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load members.'));
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
      this.memberError.set(apiErrorMessage(error, 'Failed to add member.'));
      return;
    }
    this.memberForm.reset({ email: '', password: '', displayName: '', roleId: '' });
    this.members.reload();
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
        throw new Error(apiErrorMessage(error, 'Failed to load invitations.'));
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
      this.inviteError.set(apiErrorMessage(error, 'Failed to create the invite.'));
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
      this.inviteError.set(apiErrorMessage(error, 'Failed to mint a new link.'));
      return;
    }
    this.inviteLink.set({ email: data.invite.email, url: this.joinUrl(data.invite_token) });
    this.inviteLinkCopied.set(false);
    this.invites.reload();
  }

  protected async revokeInvite(invite: Invite): Promise<void> {
    const { error } = await this.api.revokeInvite(invite.id);
    if (error) {
      this.inviteError.set(apiErrorMessage(error, 'Failed to revoke the invite.'));
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
      this.memberError.set(apiErrorMessage(error, 'Failed to change role.'));
      return;
    }
    this.members.reload();
  }

  protected async removeMember(userId: string): Promise<void> {
    if (!confirm('Remove this member? Their active sessions will be revoked.')) {
      return;
    }
    const { error } = await this.api.deleteMember(userId);
    if (error) {
      this.memberError.set(apiErrorMessage(error, 'Failed to remove member.'));
      return;
    }
    this.members.reload();
  }

  protected readonly devices = resource({
    loader: async () => {
      const { data, error } = await this.api.listPairedDevices();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load paired devices.'));
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

  // Each member row expands in place to reveal that member's devices and a
  // per-member "Pair a device" action, so every member gets their own QR.
  protected readonly expandedUserId = signal<string | null>(this.auth.userId());

  protected toggleExpanded(userId: string): void {
    this.expandedUserId.update((current) => (current === userId ? null : userId));
  }

  protected isExpanded(userId: string): boolean {
    return this.expandedUserId() === userId;
  }

  /** Own device: always. Another member's: needs device management, and an owner
   *  can only ever pair their own device (matches the server rule). */
  protected canPairForMember(member: Member): boolean {
    if (member.user_id === this.currentUserId()) {
      return true;
    }
    return this.canManageDevices() && member.role !== 'owner';
  }

  protected readonly pairingUserId = signal<string | null>(null);
  protected readonly pairingError = signal<string | null>(null);
  protected readonly pairingSession = signal<PairingSessionView | null>(null);

  protected readonly revokingId = signal<string | null>(null);
  protected readonly revokeError = signal<string | null>(null);

  protected async pairDeviceFor(member: Member): Promise<void> {
    if (this.pairingUserId()) {
      return;
    }

    this.pairingUserId.set(member.user_id);
    this.pairingError.set(null);
    this.pairingSession.set(null);

    // Pairing your own device sends no target; the server treats that as self and
    // only requires membership. A different member is an explicit on-behalf mint.
    const onBehalf = member.user_id !== this.currentUserId();
    const { data, error } = await this.api.createPairingSession(
      onBehalf ? member.user_id : undefined,
    );

    if (error || !data) {
      this.pairingUserId.set(null);
      this.pairingError.set(apiErrorMessage(error, 'Failed to create a pairing session.'));
      return;
    }

    const qrImageDataUrl = await QRCode.toDataURL(data.qr_payload, { width: 240 });

    this.pairingUserId.set(null);
    this.expandedUserId.set(member.user_id);
    this.pairingSession.set({
      userId: member.user_id,
      qrPayload: data.qr_payload,
      qrImageDataUrl,
      expiresAt: data.expires_at,
    });
  }

  protected dismissPairing(): void {
    this.pairingSession.set(null);
  }

  protected async revokeDevice(deviceId: string): Promise<void> {
    if (this.revokingId()) {
      return;
    }

    this.revokingId.set(deviceId);
    this.revokeError.set(null);

    const { error } = await this.api.revokePairedDevice(deviceId);

    this.revokingId.set(null);

    if (error) {
      this.revokeError.set(apiErrorMessage(error, 'Failed to revoke device.'));
      return;
    }

    this.devices.reload();
  }
}
