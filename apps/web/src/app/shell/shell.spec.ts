import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { Shell } from './shell';

describe('Shell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Shell],
      providers: [
        // A stub route so the nav-link click test's navigation can resolve.
        provideRouter([{ path: 'overview', children: [] }]),
        { provide: AuthService, useValue: { role: () => 'owner', hasRight: () => true, logout: () => undefined, refreshRights: async () => undefined } },
      ],
    }).compileComponents();
  });

  it('starts with the mobile menu closed and toggles it', () => {
    const component = TestBed.createComponent(Shell).componentInstance;

    expect(component['menuOpen']()).toBe(false);
    component['toggleMenu']();
    expect(component['menuOpen']()).toBe(true);
    component['toggleMenu']();
    expect(component['menuOpen']()).toBe(false);
  });

  it('closes the menu when a nav link is selected', async () => {
    const fixture = TestBed.createComponent(Shell);
    const component = fixture.componentInstance;
    component['toggleMenu']();
    fixture.detectChanges();

    const link: HTMLAnchorElement = fixture.nativeElement.querySelector('.shell__nav-link');
    link.click();
    // Let the routerLink navigation settle before teardown destroys the injector.
    await fixture.whenStable();

    expect(component['menuOpen']()).toBe(false);
  });

  it('renders grouped sections inside a scrollable nav (M70)', () => {
    const fixture = TestBed.createComponent(Shell);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    const labels = Array.from(host.querySelectorAll('.shell__nav-section')).map(
      (el) => el.textContent?.trim(),
    );
    expect(labels).toEqual(['Money', 'AI', 'Admin']);
    // The link list is its own scroll container so long menus never trap
    // the footer off-screen.
    expect(host.querySelector('nav.shell__nav-scroll')).not.toBeNull();
    expect(host.querySelectorAll('.shell__nav-link').length).toBe(21); // 20 pages + System Health
  });

  it('hides the Households link without system.admin (#180)', () => {
    TestBed.overrideProvider(AuthService, {
      useValue: {
        role: () => 'owner',
        hasRight: (right: string) => right !== 'system.admin',
        logout: () => undefined,
        refreshRights: async () => undefined,
      },
    });
    const fixture = TestBed.createComponent(Shell);
    fixture.detectChanges();
    const labels = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('.shell__nav-link'),
    ).map((el) => el.textContent?.trim());

    expect(labels).not.toContain('Households');
    expect(labels).toContain('Devices');
  });

  it('renders the scrim only while the menu is open', () => {
    const fixture = TestBed.createComponent(Shell);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).toBeNull();

    fixture.componentInstance['toggleMenu']();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).not.toBeNull();
  });
  // ADR 0074: the dashboard and the box carry their own build numbers and are
  // compatible when their CONTRACTS (MAJOR.MINOR) match. Warning on a differing
  // BUILD was the bug this scheme exists to remove, so the first case below is
  // the one that matters.
  describe('version reporting', () => {
    const stubVersions = (options: { box?: string | null; app?: string | null }) => {
      vi.stubGlobal('fetch', (url: string) => {
        if (url === '/api/v1/health') {
          return options.box === null || options.box === undefined
            ? Promise.reject(new Error('unreachable'))
            : Promise.resolve({ ok: true, json: () => Promise.resolve({ version: options.box }) });
        }
        if (url === '/VERSION') {
          return options.app === null || options.app === undefined
            ? Promise.resolve({ ok: false, text: () => Promise.resolve('') })
            : Promise.resolve({ ok: true, text: () => Promise.resolve(`${options.app}\n`) });
        }
        return Promise.reject(new Error(`unexpected fetch: ${url}`));
      });
    };

    afterEach(() => vi.unstubAllGlobals());

    it('does not warn when only the build differs', async () => {
      stubVersions({ app: '0.157.2', box: '0.157.9' });
      const fixture = TestBed.createComponent(Shell);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.componentInstance['versionMismatch']()).toBe(false);
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.shell__version-warning')).toBeNull();
      expect(host.querySelector('.shell__version')?.textContent).toContain('v0.157.2');
      expect(host.querySelector('.shell__version')?.textContent).toContain('box v0.157.9');
    });

    it('warns when the contracts differ', async () => {
      stubVersions({ app: '0.157.2', box: '0.158.0' });
      const fixture = TestBed.createComponent(Shell);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.componentInstance['versionMismatch']()).toBe(true);
      expect(
        (fixture.nativeElement as HTMLElement).querySelector('.shell__version-warning'),
      ).not.toBeNull();
    });

    it('shows nothing and never warns when its own version is unavailable', async () => {
      // `ng serve` and the tests have no /VERSION file; a missing badge is the
      // correct degradation, and a half-known pair must not be called a
      // mismatch.
      stubVersions({ app: null, box: '0.157.9' });
      const fixture = TestBed.createComponent(Shell);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.componentInstance['versionMismatch']()).toBe(false);
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.shell__version')).toBeNull();
      expect(host.querySelector('.shell__version-warning')).toBeNull();
    });

    it('survives an unreachable box', async () => {
      stubVersions({ app: '0.157.2', box: null });
      const fixture = TestBed.createComponent(Shell);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.componentInstance['versionMismatch']()).toBe(false);
      expect(
        (fixture.nativeElement as HTMLElement).querySelector('.shell__version')?.textContent,
      ).toContain('v0.157.2');
    });
  });
});
