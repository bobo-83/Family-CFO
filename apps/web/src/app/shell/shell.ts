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
        {
          path: '/overview',
          label: $localize`:Sidebar nav item|Link to the household overview page:Overview`,
        },
        {
          path: '/chat',
          label: $localize`:Sidebar nav item|Link to the AI advisor chat page:Advisor`,
          right: 'advisor.use',
        },
      ],
    },
    {
      label: $localize`:Sidebar nav group|Heading over the money pages:Money`,
      items: [
        {
          path: '/accounts',
          label: $localize`:Sidebar nav item|Link to the bank and asset accounts page:Accounts`,
        },
        {
          path: '/transactions',
          label: $localize`:Sidebar nav item|Link to the transactions ledger page:Transactions`,
        },
        {
          path: '/bills',
          label: $localize`:Sidebar nav item|Link to the recurring bills page:Bills`,
        },
        {
          path: '/loans',
          label: $localize`:Sidebar nav item|Link to the debts and loans page:Debts & loans`,
        },
        {
          path: '/income-tax',
          label: $localize`:Sidebar nav item|Link to the income and tax page:Income & tax`,
        },
        {
          path: '/budgets',
          label: $localize`:Sidebar nav item|Link to the budget envelopes page:Budgets`,
        },
        {
          path: '/categories',
          label: $localize`:Sidebar nav item|Link to the spending categories page:Categories`,
        },
        {
          path: '/goals',
          label: $localize`:Sidebar nav item|Link to the savings goals page:Goals`,
        },
      ],
    },
    {
      label: $localize`:Sidebar nav group|Heading over the AI advisor pages:AI`,
      items: [
        {
          path: '/memory',
          label: $localize`:Sidebar nav item|Link to what the advisor remembers:Advisor memory`,
          right: 'advisor.manage',
        },
        {
          path: '/ai-runtime',
          label: $localize`:Sidebar nav item|Link to the AI model runtime settings:AI runtime`,
          right: 'ai_runtime.manage',
        },
      ],
    },
    {
      label: $localize`:Sidebar nav group|Heading over the administration pages:Admin`,
      items: [
        {
          path: '/imports',
          label: $localize`:Sidebar nav item|Link to the statement imports page:Imports`,
          right: 'imports.manage',
        },
        {
          path: '/reports',
          label: $localize`:Sidebar nav item|Link to the reports page:Reports`,
          right: 'reports.manage',
        },
        {
          path: '/backups',
          label: $localize`:Sidebar nav item|Link to the backups page:Backups`,
          right: 'backups.manage',
        },
        {
          path: '/users',
          label: $localize`:Sidebar nav item|Link to the household members page:Users`,
          right: 'members.manage',
        },
        {
          path: '/roles',
          label: $localize`:Sidebar nav item|Link to the roles and rights page:Roles`,
          right: 'roles.manage',
        },
        {
          path: '/devices',
          label: $localize`:Sidebar nav item|Link to the paired devices page:Devices`,
        },
        // #180: operator hosting — only system admins ever see this.
        {
          path: '/households',
          label: $localize`:Sidebar nav item|Link to the hosted households page:Households`,
          right: 'system.admin',
        },
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
