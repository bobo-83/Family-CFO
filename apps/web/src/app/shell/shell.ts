import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly role = this.auth.role;

  protected readonly navItems = [
    { path: '/overview', label: 'Overview' },
    { path: '/chat', label: 'Ask the Advisor' },
    { path: '/accounts', label: 'Accounts' },
    { path: '/goals', label: 'Goals' },
    { path: '/reports', label: 'Reports' },
    { path: '/transactions', label: 'Transactions' },
    { path: '/imports', label: 'Imports' },
    { path: '/ai-runtime', label: 'AI Runtime' },
    { path: '/backups', label: 'Backups' },
    { path: '/users', label: 'Users' },
  ];

  protected logout(): void {
    this.auth.logout();
    void this.router.navigateByUrl('/login');
  }
}
