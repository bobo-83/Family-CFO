import { Component } from '@angular/core';
import { ShellPlaceholder } from '../../shared/shell-placeholder';

@Component({
  selector: 'app-reports',
  imports: [ShellPlaceholder],
  template: `<app-shell-placeholder
    title="Reports"
    note="Weekly, monthly, and annual reports arrive in M8. There is no reports API yet."
  />`,
})
export class Reports {}
