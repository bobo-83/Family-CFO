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
        { provide: AuthService, useValue: { role: () => 'owner', logout: () => undefined } },
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

  it('renders the scrim only while the menu is open', () => {
    const fixture = TestBed.createComponent(Shell);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).toBeNull();

    fixture.componentInstance['toggleMenu']();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.shell__scrim')).not.toBeNull();
  });
});
