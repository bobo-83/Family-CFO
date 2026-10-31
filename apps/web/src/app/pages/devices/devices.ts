import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import type { Member, PairedDevice } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-devices',
  imports: [DatePipe, MatCardModule, MatButtonModule],
  templateUrl: './devices.html',
  styleUrl: './devices.scss',
})
export class Devices {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  protected readonly devices = signal<PairedDevice[]>([]);
  protected readonly members = signal<Member[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly canRevoke = () => this.auth.hasRight('devices.manage');

  constructor() {
    void this.load();
  }

  protected memberName(userId: string | null | undefined): string {
    return this.members().find((member) => member.user_id === userId)?.display_name
      ?? $localize`Former or unknown member`;
  }

  private async load(): Promise<void> {
    this.loadError.set(null);
    const { data, error } = await this.api.listPairedDevices();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, $localize`Failed to load paired devices.`));
      return;
    }
    this.devices.set(data.devices);
    const { data: memberData } = await this.api.listMembers();
    if (memberData) {
      this.members.set(memberData.members);
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
      this.actionError.set(apiErrorMessage(error, $localize`Failed to revoke the device.`));
      return;
    }
    await this.load();
  }
}
