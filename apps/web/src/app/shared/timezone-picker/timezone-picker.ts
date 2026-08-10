import { Component, input, output, signal } from '@angular/core';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import {
  TIMEZONE_BOX_DEFAULT,
  knownTimezones,
  timezoneOptionsFor,
  timezoneShortlist,
} from '../timezones';

/**
 * #41/#93: the search-box-over-a-shortlist zone picker, shared by the two
 * moments a household's zone is chosen — the Overview settings card and the
 * invite page (#93), where the person who actually knows the answer is present.
 *
 * Presentational on purpose: it owns the search box and the list, and emits a
 * choice. Saving it belongs to the caller, because the two callers do very
 * different things with it — one PATCHes an existing household, the other
 * carries it into a form that has not been submitted yet.
 */
@Component({
  selector: 'app-timezone-picker',
  imports: [MatAutocompleteModule, MatFormFieldModule, MatInputModule],
  templateUrl: './timezone-picker.html',
  styleUrl: './timezone-picker.scss',
})
export class TimezonePicker {
  /** The selected zone; '' when none is set. */
  readonly value = input<string>('');
  readonly disabled = input<boolean>(false);

  /** A zone ID, or TIMEZONE_BOX_DEFAULT for "follow the box's own zone". */
  readonly picked = output<string>();

  /** #43: the way BACK to inheriting the box's zone once one has been picked. */
  protected readonly boxDefaultOption = $localize`:Household timezone option|First entry in the time-zone picker; picking it clears the household's zone so dates follow the server's own zone again:Use the box's zone`;

  /** What has been typed into the search box; null means "not searching". */
  protected readonly query = signal<string | null>(null);

  private readonly allTimezones = knownTimezones();
  private readonly shortlist = timezoneShortlist();

  /** What the field shows: the search text while typing, else the saved zone. */
  protected display(): string {
    return this.query() ?? this.value();
  }

  protected options(): string[] {
    return timezoneOptionsFor(this.query() ?? '', this.value(), this.allTimezones, this.shortlist);
  }

  /** What a row reads: a zone ID verbatim, or #43's "back to the box" entry. */
  protected optionLabel(option: string): string {
    return option === TIMEZONE_BOX_DEFAULT ? this.boxDefaultOption : option;
  }

  protected choose(option: string): void {
    this.query.set(null); // done searching — show the value again
    if (!option) {
      return;
    }
    this.picked.emit(option);
  }
}
