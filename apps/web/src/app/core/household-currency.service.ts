import { Injectable, computed, inject, signal } from '@angular/core';
import type { HouseholdContext } from '../api-client';
import { apiErrorMessage } from '../shared/api-error';
import { ApiService } from './api.service';
import { authState } from './token-store';

/**
 * The session a cached value belongs to (#156). Login and signup replace the
 * token, logout clears the state, so a value keyed this way can never outlive
 * the household it was fetched for — a root service outlives all of those.
 * "no-session" keeps the unauthenticated case (and unit tests without a stored
 * session) consistent without pretending it is a household.
 */
export function householdSessionKey(): string {
  const state = authState();
  return state ? `${state.householdId}:${state.accessToken}` : 'no-session';
}

/**
 * The household's base currency, for every screen that must not guess it (#156,
 * ADR 0075). `GET /household` is the one endpoint that carries it, so this is
 * one memoised fetch per session: the Overview seeds it from the context it
 * already loads, and a screen entered directly (Accounts, Goals) fetches it once.
 *
 * Rules, each pinned by a test:
 * - keyed by household AND session: a logout or a login as another household
 *   drops the value, and a completion that lands after the session changed is
 *   discarded rather than cached under the new session;
 * - single-flight: concurrent callers share one in-flight request;
 * - successes only are cached; a failure is reported and cleared, so the next
 *   call retries instead of pinning the error for the rest of the session;
 * - never a default. `currency()` is null until known, and callers keep their
 *   forms closed until then — a literal 'USD' is how a EUR household got USD
 *   accounts (#152).
 */
@Injectable({ providedIn: 'root' })
export class HouseholdCurrencyService {
  private readonly api = inject(ApiService);

  private readonly cached = signal<{ key: string; currency: string } | null>(null);
  private readonly failed = signal<{ key: string; message: string } | null>(null);
  private inflight: { key: string; promise: Promise<string | null> } | null = null;

  /** The base currency for the CURRENT session, or null while unknown. */
  readonly currency = computed(() => {
    const cached = this.cached();
    return cached && cached.key === householdSessionKey() ? cached.currency : null;
  });

  /** Why the last fetch for the current session failed, or null. */
  readonly error = computed(() => {
    const failed = this.failed();
    return failed && failed.key === householdSessionKey() ? failed.message : null;
  });

  /**
   * A screen that already holds the live context hands it over — no second
   * fetch. `requestedIn` is the session key captured BEFORE that screen's
   * request started: a response that lands after a logout and a login as
   * another household would otherwise be tagged with the new session (review
   * of #158). A context for a different household is refused the same way.
   */
  seed(context: HouseholdContext, requestedIn: string): void {
    if (!this.belongsToCurrentSession(context, requestedIn)) {
      return;
    }
    this.cached.set({ key: requestedIn, currency: context.currency });
    this.failed.set(null);
  }

  private belongsToCurrentSession(context: HouseholdContext, requestedIn: string): boolean {
    if (requestedIn !== householdSessionKey()) {
      return false;
    }
    const householdId = authState()?.householdId;
    return householdId == null || context.household_id === householdId;
  }

  /** Resolve the currency, fetching at most once per session at a time. */
  async load(): Promise<string | null> {
    const key = householdSessionKey();
    const cached = this.cached();
    if (cached && cached.key === key) {
      return cached.currency;
    }
    if (this.inflight && this.inflight.key === key) {
      return this.inflight.promise;
    }
    const promise = this.fetch(key);
    this.inflight = { key, promise };
    try {
      return await promise;
    } finally {
      if (this.inflight?.promise === promise) {
        this.inflight = null;
      }
    }
  }

  private async fetch(key: string): Promise<string | null> {
    const { data, error } = await this.api.getHouseholdContext();
    if (key !== householdSessionKey()) {
      // Late completion for a session that is no longer current: not this
      // household's currency, so neither cached nor reported.
      return null;
    }
    if (data && !this.belongsToCurrentSession(data, key)) {
      return null;
    }
    if (error || !data) {
      this.failed.set({
        key,
        message: apiErrorMessage(
          error,
          $localize`:Error message|The household's base currency could not be loaded:Could not load the household currency.`,
        ),
      });
      return null;
    }
    this.cached.set({ key, currency: data.currency });
    this.failed.set(null);
    return data.currency;
  }
}
