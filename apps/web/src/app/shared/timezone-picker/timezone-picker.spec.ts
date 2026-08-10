import { TestBed } from '@angular/core/testing';
import { TimezonePicker } from './timezone-picker';
import { TIMEZONE_BOX_DEFAULT } from '../timezones';

/**
 * #93: the list, shortlist and search used to live on the Overview page. They
 * moved here when the invite page needed the same picker, so these are the
 * tests that came with them plus the ones the new caller needs.
 */
describe('TimezonePicker (#41, shared by #93)', () => {
  function render(value = '') {
    TestBed.configureTestingModule({ imports: [TimezonePicker] });
    const fixture = TestBed.createComponent(TimezonePicker);
    fixture.componentRef.setInput('value', value);
    fixture.detectChanges();
    return fixture;
  }

  it('offers the common zones before anything is typed', () => {
    const component = render('America/New_York').componentInstance;

    const shortlist = component['options']();

    expect(shortlist).toContain('Europe/London');
    expect(shortlist).toContain('America/New_York');
    // A shortlist people can actually scan, not the whole zone database.
    expect(shortlist.length).toBeLessThan(25);
  });

  it('keeps a zone outside the shortlist visible', () => {
    const component = render('Pacific/Chatham').componentInstance;

    // #43's "use the box's zone" row leads; the current zone follows it.
    expect(component['options']()[1]).toBe('Pacific/Chatham');
  });

  // #43: the inherit state was one-way — reachable only by never having
  // chosen a zone.
  it("offers the box's own zone at the top once a zone is set", () => {
    const component = render('Europe/London').componentInstance;

    const shortlist = component['options']();

    expect(shortlist[0]).toBe(TIMEZONE_BOX_DEFAULT);
    expect(component['optionLabel'](shortlist[0])).toContain("box's zone");
    // A zone ID is an identifier and is shown verbatim.
    expect(component['optionLabel']('Europe/London')).toBe('Europe/London');
  });

  it('does not offer it when there is nothing to clear', () => {
    const component = render('').componentInstance;

    expect(component['options']()).not.toContain(TIMEZONE_BOX_DEFAULT);
  });

  it('searches every zone, matching spaces against underscores', () => {
    const component = render('UTC').componentInstance;

    component['query'].set('new york');
    expect(component['options']()).toContain('America/New_York');

    // Never enough hits at once to drown the panel.
    component['query'].set('a');
    expect(component['options']().length).toBeLessThanOrEqual(50);
  });

  it('emits the choice and stops searching', () => {
    const fixture = render('UTC');
    const component = fixture.componentInstance;
    const picked: string[] = [];
    component.picked.subscribe((zone: string) => picked.push(zone));

    component['query'].set('lond');
    component['choose']('Europe/London');

    expect(picked).toEqual(['Europe/London']);
    // Back to showing the value rather than the half-typed search.
    expect(component['display']()).toBe('UTC');
  });
});
