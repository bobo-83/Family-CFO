import { DatePipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
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
  imports: [DatePipe],
  templateUrl: './users.html',
  styleUrl: './users.scss',
})
export class Users {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly canPairDevices = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };
  protected readonly canRevokeDevices = () => this.auth.role() === 'owner';

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
