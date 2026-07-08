import { Component, input } from '@angular/core';

/**
 * Honest scaffolding for M5 nav destinations whose backend doesn't exist
 * yet (M6 pairing, M7 imports, M8 reports/backups). States what's missing
 * rather than simulating functionality.
 */
@Component({
  selector: 'app-shell-placeholder',
  template: `
    <h1>{{ title() }}</h1>
    <p class="shell-placeholder__note">{{ note() }}</p>
  `,
  styles: [
    `
      .shell-placeholder__note {
        color: #5b6472;
        max-width: 40rem;
      }
    `,
  ],
})
export class ShellPlaceholder {
  readonly title = input.required<string>();
  readonly note = input.required<string>();
}
