import { Component } from '@angular/core';
import { ShellPlaceholder } from '../../shared/shell-placeholder';

@Component({
  selector: 'app-transactions',
  imports: [ShellPlaceholder],
  template: `<app-shell-placeholder
    title="Transaction Review"
    note="A transaction review queue with categorization and edits is planned for M7 (imports). The backend today only exposes a read-only transaction list."
  />`,
})
export class Transactions {}
