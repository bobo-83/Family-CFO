import { Component } from '@angular/core';
import { ShellPlaceholder } from '../../shared/shell-placeholder';

@Component({
  selector: 'app-backups',
  imports: [ShellPlaceholder],
  template: `<app-shell-placeholder
    title="Backups"
    note="Encrypted backup management arrives in M8. There is no backup API yet."
  />`,
})
export class Backups {}
