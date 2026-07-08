import { Component } from '@angular/core';
import { ShellPlaceholder } from '../../shared/shell-placeholder';

@Component({
  selector: 'app-users',
  imports: [ShellPlaceholder],
  template: `<app-shell-placeholder
    title="Users & Devices"
    note="Household membership management and paired-device revocation both arrive with M6 (pairing). There is no membership-management or pairing API yet."
  />`,
})
export class Users {}
