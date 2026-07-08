import { Component } from '@angular/core';
import { ShellPlaceholder } from '../../shared/shell-placeholder';

@Component({
  selector: 'app-imports',
  imports: [ShellPlaceholder],
  template: `<app-shell-placeholder
    title="Import Review"
    note="CSV/PDF import review lands in M7. There is no imports API yet."
  />`,
})
export class Imports {}
