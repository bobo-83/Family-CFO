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
  private readonly memberLookup = signal<'loading' | 'loaded' | 'failed'>('loading');
  private loadGeneration = 0;
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly canRevoke = () => this.auth.hasRight('devices.manage');

  constructor() {
    void this.load();
  }

  protected memberName(userId: string | null | undefined): string {
    if (this.memberLookup() === 'loading') {
      return $localize`Loading member…`;
    }
    if (this.memberLookup() === 'failed') {
      return $localize`Member name unavailable`;
    }
    return this.members().find((member) => member.user_id === userId)?.display_name
      ?? $localize`Former or unknown member`;
  }

  private async load(): Promise<void> {
    const generation = ++this.loadGeneration;
    this.loadError.set(null);
    this.memberLookup.set('loading');
    this.members.set([]);
    try {
      const { data, error } = await this.api.listPairedDevices();
      if (generation !== this.loadGeneration) {
        return;
      }
      this.loading.set(false);
      if (error || !data) {
        this.loadError.set(apiErrorMessage(error, $localize`Failed to load paired devices.`));
        return;
      }
      this.devices.set(data.devices);
      try {
        const { data: memberData, error: memberError } = await this.api.listMembers();
        if (generation !== this.loadGeneration) {
          return;
        }
        if (memberError || !memberData) {
          this.memberLookup.set('failed');
          return;
        }
        this.members.set(memberData.members);
        this.memberLookup.set('loaded');
      } catch {
        // Owner names are optional enrichment; keep device management usable.
        if (generation === this.loadGeneration) {
          this.memberLookup.set('failed');
        }
      }
    } catch (error) {
      if (generation === this.loadGeneration) {
        this.loading.set(false);
        this.loadError.set(apiErrorMessage(error, $localize`Failed to load paired devices.`));
      }
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
