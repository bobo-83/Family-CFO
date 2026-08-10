// --- #41: the zone a household reckons "today" in ---------------------------
// Zone IDs are identifiers, so none of the values below are ever translated.
//
// #93: extracted from the Overview card so the invite page can offer the same
// picker. There are now two moments a household's zone gets chosen — settings
// and acceptance — and one list, one shortlist and one search behind both.

/**
 * Reachable without typing anything. The full IANA list is ~600 entries — one
 * flat picker of it is a wall, not a choice — so everything outside this set
 * lives behind the search box.
 */
export const CURATED_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Toronto',
  'Europe/London',
  'Europe/Dublin',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Vilnius',
  'Asia/Ho_Chi_Minh',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
  'UTC',
];

/** Search hits rendered at once — enough to find a zone, few enough to scan. */
export const TIMEZONE_RESULT_LIMIT = 50;

/**
 * #43: the "go back to the box's own zone" row. A sentinel rather than '' so
 * it can never collide with a real zone ID, be typed into the search box, or
 * be mistaken for the empty value the field shows when nothing is set.
 */
export const TIMEZONE_BOX_DEFAULT = ' box-default';

/**
 * Every zone this browser knows. `Intl.supportedValuesOf` is recent, so an
 * older engine falls back to the curated set rather than offering nothing.
 */
export function knownTimezones(): string[] {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.('timeZone');
    if (zones && zones.length > 0) {
      return zones;
    }
  } catch {
    // Some engines have the method but throw on the key; treat that as absent.
  }
  return CURATED_TIMEZONES;
}

/** This browser's own zone — nearly always the one the household means. */
export function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/**
 * What the list offers before a single key is pressed: this browser's own zone
 * first (the overwhelmingly likely answer), then the usual suspects.
 */
export function timezoneShortlist(): string[] {
  return [...new Set([browserTimezone(), ...CURATED_TIMEZONES].filter((zone) => !!zone))] as string[];
}

/**
 * The rows to show for a search box holding `query` while `current` is the
 * selected zone ('' when none is).
 */
export function timezoneOptionsFor(
  query: string,
  current: string,
  allZones: string[],
  shortlist: string[],
): string[] {
  // "America/New_York" is the identifier but "new york" is what gets typed.
  const needle = query.trim().toLowerCase().replace(/\s+/g, '_');
  if (!needle) {
    // Nothing typed yet. A zone set outside the shortlist still has to be
    // visible — otherwise the current value looks unselected.
    const zones = current && !shortlist.includes(current) ? [current, ...shortlist] : shortlist;
    // #43: only worth offering when there is something to clear, and only in
    // the unsearched list — a row that is not a zone would read as noise among
    // search hits.
    return current ? [TIMEZONE_BOX_DEFAULT, ...zones] : zones;
  }
  return allZones.filter((zone) => zone.toLowerCase().includes(needle)).slice(0, TIMEZONE_RESULT_LIMIT);
}

/**
 * Why the zone matters, in the words shown next to the picker. One message,
 * two callers (#93): the settings card and the invite page explain the same
 * thing, and a second copy would be a second translation to keep in step.
 */
export const TIMEZONE_HINT = $localize`:Household timezone hint|Explains what the time zone setting changes:Bills, due dates and Safe to Spend use this zone to decide what “today” means.`;
