import { DatePipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import QRCode from 'qrcode';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

interface PairingSessionView {
  qrPayload: string;
  qrImageDataUrl: string;
  expiresAt: string;
}

@Component({
  selector: 'app-users',
  imports: [DatePipe, ReactiveFormsModule],
  templateUrl: './users.html',
  styleUrl: './users.scss',
})
export class Users {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  // ADR 0034: sections gate by RIGHT. Pairing your own device is always allowed.
  protected readonly canPairDevices = () => true;
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

  protected readonly pairing = signal(false);
  protected readonly pairingError = signal<string | null>(null);
  protected readonly pairingSession = signal<PairingSessionView | null>(null);

  protected readonly revokingId = signal<string | null>(null);
  protected readonly revokeError = signal<string | null>(null);

  protected async pairDevice(): Promise<void> {
    if (this.pairing()) {
      return;
    }

    this.pairing.set(true);
    this.pairingError.set(null);
    this.pairingSession.set(null);

    const { data, error } = await this.api.createPairingSession();

    if (error || !data) {
      this.pairing.set(false);
      this.pairingError.set(apiErrorMessage(error, 'Failed to create a pairing session.'));
      return;
    }

    const qrImageDataUrl = await QRCode.toDataURL(data.qr_payload, { width: 240 });

    this.pairing.set(false);
    this.pairingSession.set({
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
