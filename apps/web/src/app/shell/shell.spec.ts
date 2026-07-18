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
        { provide: AuthService, useValue: { role: () => 'owner', hasRight: () => true, logout: () => undefined } },
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
    expect(labels).toEqual(['Money', 'Advisor', 'Admin']);
    // The link list is its own scroll container so long menus never trap
    // the footer off-screen.
    expect(host.querySelector('nav.shell__nav-scroll')).not.toBeNull();
    expect(host.querySelectorAll('.shell__nav-link').length).toBe(19); // 18 pages + System Health
  });

  it('renders the scrim only while the menu is open', () => {
    const fixture = TestBed.createComponent(Shell);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).toBeNull();

    fixture.componentInstance['toggleMenu']();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).not.toBeNull();
  });
});
