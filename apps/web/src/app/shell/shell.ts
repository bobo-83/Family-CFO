import { Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  // ADR 0074: the dashboard carries its own build number, so the footer shows
  // TWO versions — this build, and the box it is talking to. They are expected
  // to differ in the last field; only a differing contract means trouble.
  //
  // Both are plain fetches: same origin, unauthenticated, and a failed check
  // must never break the shell (it degrades to no badge).
  protected readonly serverVersion = signal<string | null>(null);
  protected readonly appVersion = signal<string | null>(null);

  /**
   * What a composed version looks like: contract plus build, all integers.
   * `/VERSION` is a file our own nginx serves from an exact-match location, but
   * "nginx returned 200" is not "this is a version" — a TLS reverse proxy in
   * front, an SSO interstitial, or a later edit to that location would each
   * return a body that is not one. Unvalidated, such a body would be painted
   * into the badge AND compared as a contract, raising a mismatch warning about
   * nothing. Anything that does not match degrades to the same "no badge"
   * posture as a failed fetch.
   */
  private static readonly VERSION_PATTERN = /^\d+\.\d+\.\d+$/;

  /**
   * The compatibility contract — the MAJOR.MINOR prefix. Two deployables that
   * agree here can talk to each other whatever their build numbers are.
   */
  private static contractOf(version: string): string {
    return version.split('.').slice(0, 2).join('.');
  }

  /**
   * True only when the dashboard and the box name DIFFERENT contracts. A
   * differing build is the normal, healthy case now — warning about it was the
   * whole problem ADR 0074 set out to fix.
   */
  protected readonly versionMismatch = computed(() => {
    const app = this.appVersion();
    const box = this.serverVersion();
    if (!app || !box) {
      return false;
    }
    return Shell.contractOf(app) !== Shell.contractOf(box);
  });

  private loadVersion(): void {
    void fetch('/api/v1/health')
      .then((response) => response.json())
      .then((health: { version?: string }) => {
        this.serverVersion.set(health.version ?? null);
      })
      .catch(() => this.serverVersion.set(null));

    // Written into the nginx root by docker/web.Dockerfile. Absent under `ng
    // serve` and in tests, where the badge simply does not render.
    void fetch('/VERSION')
      .then((response) => (response.ok ? response.text() : null))
      .then((text) => {
        const trimmed = text?.trim() ?? '';
        this.appVersion.set(Shell.VERSION_PATTERN.test(trimmed) ? trimmed : null);
      })
      .catch(() => this.appVersion.set(null));
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
        // #97: your own password — no right, because everyone has one.
        {
          path: '/change-password',
          label: $localize`:Sidebar nav item|Link to the change-your-password page:Change password`,
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
