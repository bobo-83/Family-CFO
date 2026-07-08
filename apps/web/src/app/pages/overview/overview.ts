import { DecimalPipe } from '@angular/common';
import { Component, inject, resource } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-overview',
  imports: [DecimalPipe],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class Overview {
  private readonly api = inject(ApiService);

  protected readonly household = resource({
    loader: async () => {
      const { data, error } = await this.api.getHouseholdContext();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load household overview.'));
      }
      return data;
    },
  });

  protected readonly formatMoney = formatMoney;
}
