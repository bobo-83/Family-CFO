import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { DomSanitizer, type SafeHtml } from '@angular/platform-browser';
import * as QRCode from 'qrcode';
import type { PairedDevice } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-devices',
  imports: [DatePipe],
  templateUrl: './devices.html',
  styleUrl: './devices.scss',
})
export class Devices {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly sanitizer = inject(DomSanitizer);

  protected readonly devices = signal<PairedDevice[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);

  // The active pairing code (M83a): SVG QR of the server's qr_payload.
  protected readonly qrSvg = signal<SafeHtml | null>(null);
  protected readonly qrExpiresAt = signal<string | null>(null);
  protected readonly fingerprint = signal<string | null>(null);

  protected readonly canPair = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };
  protected readonly canRevoke = () => this.auth.role() === 'owner';

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loadError.set(null);
    const { data, error } = await this.api.listPairedDevices();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load paired devices.'));
      return;
    }
    this.devices.set(data.devices);
  }

  protected async showPairingCode(): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set('qr');
    this.actionError.set(null);
    const { data, error } = await this.api.createPairingSession();
    this.busy.set(null);
    if (error || !data) {
      this.actionError.set(apiErrorMessage(error, 'Failed to create a pairing code.'));
      return;
    }
    const svg = await QRCode.toString(data.qr_payload, { type: 'svg', margin: 1 });
    this.qrSvg.set(this.sanitizer.bypassSecurityTrustHtml(svg));
    this.qrExpiresAt.set(data.expires_at);
    try {
      const payload = JSON.parse(data.qr_payload) as { certificate_sha256?: string | null };
      this.fingerprint.set(payload.certificate_sha256 ?? null);
    } catch {
      this.fingerprint.set(null);
    }
  }

  protected async revoke(device: PairedDevice): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(device.id);
    this.actionError.set(null);
    const { error } = await this.api.revokePairedDevice(device.id);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to revoke the device.'));
      return;
    }
    await this.load();
  }
}
