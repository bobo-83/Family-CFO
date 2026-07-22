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
    void this.auth.refreshRights();
  }

  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly role = this.auth.role;

  /** Mobile drawer state; the sidebar is always visible on desktop widths. */
  protected readonly menuOpen = signal(false);

  // M70: grouped so the drawer stays scannable as pages accumulate; the
  // template renders each group under a small label. Each item names the RIGHT
  // that reveals it (ADR 0034); no right = visible to every member.
  private readonly allNavSections: {
    label: string | null;
    items: { path: string; label: string; right?: string }[];
  }[] = [
    {
      label: null,
      items: [
        { path: '/overview', label: 'Overview' },
        { path: '/chat', label: 'Advisor', right: 'advisor.use' },
      ],
    },
    {
      label: 'Money',
      items: [
        { path: '/accounts', label: 'Accounts' },
        { path: '/transactions', label: 'Transactions' },
        { path: '/bills', label: 'Bills' },
        { path: '/loans', label: 'Debts & loans' },
        { path: '/income-tax', label: 'Income & tax' },
        { path: '/budgets', label: 'Budgets' },
        { path: '/categories', label: 'Categories' },
        { path: '/goals', label: 'Goals' },
      ],
    },
    {
      label: 'AI',
      items: [
        { path: '/memory', label: 'Advisor memory', right: 'advisor.manage' },
        { path: '/ai-runtime', label: 'AI runtime', right: 'ai_runtime.manage' },
      ],
    },
    {
      label: 'Admin',
      items: [
        { path: '/imports', label: 'Imports', right: 'imports.manage' },
        { path: '/reports', label: 'Reports', right: 'reports.manage' },
        { path: '/backups', label: 'Backups', right: 'backups.manage' },
        { path: '/users', label: 'Users', right: 'members.manage' },
        { path: '/roles', label: 'Roles', right: 'roles.manage' },
        { path: '/devices', label: 'Devices' },
      ],
    },
  ];

  protected get navSections(): { label: string | null; items: { path: string; label: string }[] }[] {
    return this.allNavSections
      .map((section) => ({
        label: section.label,
        items: section.items.filter((item) => !item.right || this.auth.hasRight(item.right)),
      }))
      .filter((section) => section.items.length > 0);
  }

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
