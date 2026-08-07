import { LOCALE_ID, inject } from '@angular/core';

/**
 * #10 phase 2: send the browser to the build that speaks the household's
 * language.
 *
 * Angular's compile-time i18n produces one bundle per locale (`/en/`, `/vi/`,
 * `/lt/`), so "switch language" is a navigation, not a state change — nothing
 * in a running en bundle can render Vietnamese. Two moments matter:
 *
 *  - **Boot**: the last known language is read from localStorage BEFORE the app
 *    paints, so a returning Vietnamese household never flashes English. This is
 *    why the value is cached client-side rather than fetched: a round trip here
 *    would be the flash.
 *  - **After the household loads (or its language changes)**: `remember()`
 *    stores the authoritative value and redirects if the running bundle is
 *    wrong.
 *
 * The path and query are preserved across the hop, so a deep link survives.
 */

const STORAGE_KEY = 'family-cfo.language';
const SUPPORTED = ['en', 'vi', 'lt'] as const;
export type SupportedLocale = (typeof SUPPORTED)[number];

function isSupported(value: string | null | undefined): value is SupportedLocale {
  return !!value && (SUPPORTED as readonly string[]).includes(value);
}

/** The locale of the bundle currently running ("en-US" → "en"). */
export function runningLocale(localeId: string): SupportedLocale {
  const base = localeId.split('-')[0];
  return isSupported(base) ? base : 'en';
}

/**
 * Where the app is served from for a locale. The en bundle is served at the
 * root as well as /en/ (nginx redirects bare paths there), so both forms are
 * accepted when deciding whether a hop is needed.
 */
function localeHref(target: SupportedLocale, path: string, search: string): string {
  const stripped = path.replace(/^\/(en|vi|lt)(?=\/|$)/, '') || '/';
  return `/${target}${stripped}${search}`;
}

/**
 * Called before bootstrap. Redirects immediately when the cached language
 * disagrees with the running bundle, so the wrong language never paints.
 * Returns true when a redirect was issued (the caller should stop bootstrapping).
 */
export function redirectToCachedLocale(localeId: string): boolean {
  let cached: string | null = null;
  try {
    cached = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private mode or storage disabled: fall through to the served bundle.
    return false;
  }
  if (!isSupported(cached) || cached === runningLocale(localeId)) {
    return false;
  }
  window.location.replace(
    localeHref(cached, window.location.pathname, window.location.search),
  );
  return true;
}

/**
 * The authoritative language, learned from the household context. Stores it for
 * the next boot and hops now if this bundle can't speak it.
 */
export class LocaleRedirect {
  private readonly localeId = inject(LOCALE_ID);

  get current(): SupportedLocale {
    return runningLocale(this.localeId);
  }

  remember(language: string | null | undefined): void {
    if (!isSupported(language)) {
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Non-fatal: without storage the next boot simply starts from the served
      // bundle and hops again once the household loads.
    }
    if (language !== this.current) {
      window.location.assign(
        localeHref(language, window.location.pathname, window.location.search),
      );
    }
  }
}
