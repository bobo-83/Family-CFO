import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  // M120 (ADR 0029): the monorepo version this box runs, shown in the footer so
  // "which version is live?" never needs a terminal. Plain fetch: same origin,
  // unauthenticated, and a failed check must never break the shell.
  protected readonly serverVersion = signal<string | null>(null);

  private loadVersion(): void {
    void fetch('/api/v1/health')
      .then((response) => response.json())
      .then((health: { version?: string }) => {
        this.serverVersion.set(health.version ?? null);
      })
      .catch(() => this.serverVersion.set(null));
  }

  constructor() {
    this.loadVersion();
  }

  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly role = this.auth.role;

  /** Mobile drawer state; the sidebar is always visible on desktop widths. */
  protected readonly menuOpen = signal(false);

  // M70: grouped so the drawer stays scannable as pages accumulate; the
  // template renders each group under a small label.
  protected readonly navSections: { label: string | null; items: { path: string; label: string }[] }[] = [
    {
      label: null,
      items: [
        { path: '/overview', label: 'Overview' },
        { path: '/chat', label: 'Ask the Advisor' },
      ],
    },
    {
      label: 'Money',
      items: [
        { path: '/accounts', label: 'Accounts' },
        { path: '/transactions', label: 'Transactions' },
        { path: '/bills', label: 'Bills' },
        { path: '/loans', label: 'Debts & Loans' },
        { path: '/income-tax', label: 'Income & Tax' },
        { path: '/budgets', label: 'Budgets' },
        { path: '/categories', label: 'Categories' },
        { path: '/goals', label: 'Goals' },
      ],
    },
    {
      label: 'Advisor',
      items: [
        { path: '/memory', label: 'Advisor Memory' },
        { path: '/ai-runtime', label: 'AI Runtime' },
      ],
    },
    {
      label: 'Admin',
      items: [
        { path: '/imports', label: 'Imports' },
        { path: '/reports', label: 'Reports' },
        { path: '/backups', label: 'Backups' },
        { path: '/users', label: 'Users' },
        { path: '/devices', label: 'Devices' },
      ],
    },
  ];

  protected toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  protected closeMenu(): void {
    this.menuOpen.set(false);
  }

  protected logout(): void {
    this.auth.logout();
    void this.router.navigateByUrl('/login');
  }
}
