import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from './api.service';
import { HouseholdCurrencyService, householdSessionKey } from './household-currency.service';
import { clearAuthState, setAuthState } from './token-store';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function session(householdId: string, accessToken = 'token') {
  setAuthState({ accessToken, householdId, userId: 'u1', role: 'owner' });
}

/** A promise the test resolves by hand, to order completions against session changes. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe('HouseholdCurrencyService (#156)', () => {
  let getHouseholdContext: ReturnType<typeof vi.fn>;
  let service: HouseholdCurrencyService;

  beforeEach(() => {
    clearAuthState();
    getHouseholdContext = vi.fn();
    TestBed.configureTestingModule({
      providers: [{ provide: ApiService, useValue: { getHouseholdContext } }],
    });
    service = TestBed.inject(HouseholdCurrencyService);
  });

  afterEach(() => clearAuthState());

  it('is unknown until loaded, then caches the base currency for the session', async () => {
    session('hh-1');
    getHouseholdContext.mockResolvedValue(response({ household_id: 'hh-1', currency: 'EUR' }));

    expect(service.currency()).toBeNull();
    expect(await service.load()).toBe('EUR');
    expect(service.currency()).toBe('EUR');
    expect(await service.load()).toBe('EUR');
    expect(getHouseholdContext).toHaveBeenCalledTimes(1);
  });

  it('shares one in-flight request between concurrent callers', async () => {
    session('hh-1');
    const pending = deferred<unknown>();
    getHouseholdContext.mockReturnValue(pending.promise);

    const first = service.load();
    const second = service.load();
    pending.resolve(response({ household_id: 'hh-1', currency: 'EUR' }));

    expect(await Promise.all([first, second])).toEqual(['EUR', 'EUR']);
    expect(getHouseholdContext).toHaveBeenCalledTimes(1);
  });

  it('reports a failure, caches nothing, and retries on the next call', async () => {
    session('hh-1');
    getHouseholdContext
      .mockResolvedValueOnce(response(undefined, { error: { code: 'boom', message: 'down' } }))
      .mockResolvedValueOnce(response({ household_id: 'hh-1', currency: 'EUR' }));

    expect(await service.load()).toBeNull();
    expect(service.currency()).toBeNull();
    expect(service.error()).toBeTruthy();

    expect(await service.load()).toBe('EUR');
    expect(service.error()).toBeNull();
    expect(getHouseholdContext).toHaveBeenCalledTimes(2);
  });

  it('drops the value when the session ends or another household logs in', async () => {
    session('hh-1');
    getHouseholdContext.mockResolvedValue(response({ household_id: 'hh-1', currency: 'EUR' }));
    await service.load();
    expect(service.currency()).toBe('EUR');

    clearAuthState();
    expect(service.currency()).toBeNull();

    session('hh-2', 'other-token');
    expect(service.currency()).toBeNull();
    getHouseholdContext.mockResolvedValue(response({ household_id: 'hh-2', currency: 'VND' }));
    expect(await service.load()).toBe('VND');
    expect(getHouseholdContext).toHaveBeenCalledTimes(2);
  });

  it('discards a completion that lands after the session changed', async () => {
    session('hh-1');
    const slow = deferred<unknown>();
    getHouseholdContext.mockReturnValueOnce(slow.promise);
    const stale = service.load();

    // The user logs out and someone else logs in before hh-1's answer arrives.
    clearAuthState();
    session('hh-2', 'other-token');
    slow.resolve(response({ household_id: 'hh-1', currency: 'EUR' }));

    expect(await stale).toBeNull();
    expect(service.currency()).toBeNull();
    // The new session fetches its own.
    getHouseholdContext.mockResolvedValueOnce(response({ household_id: 'hh-2', currency: 'VND' }));
    expect(await service.load()).toBe('VND');
  });

  it('seeds from a context the Overview already loaded, for the current session only', () => {
    session('hh-1');
    service.seed({ household_id: 'hh-1', currency: 'EUR' } as never, householdSessionKey());
    expect(service.currency()).toBe('EUR');
    expect(getHouseholdContext).not.toHaveBeenCalled();

    clearAuthState();
    expect(service.currency()).toBeNull();
  });

  it('refuses a seed requested in a session that is no longer current', () => {
    session('hh-1');
    const requestedIn = householdSessionKey();
    // The Overview's request was in flight while the user logged out and
    // someone else logged in; its response must not become theirs.
    clearAuthState();
    session('hh-2', 'other-token');

    service.seed({ household_id: 'hh-1', currency: 'EUR' } as never, requestedIn);

    expect(service.currency()).toBeNull();
  });

  it("refuses a context that is not the session's household, even under the right key", async () => {
    session('hh-2', 'other-token');
    service.seed({ household_id: 'hh-1', currency: 'EUR' } as never, householdSessionKey());
    expect(service.currency()).toBeNull();

    getHouseholdContext.mockResolvedValue(response({ household_id: 'hh-1', currency: 'EUR' }));
    expect(await service.load()).toBeNull();
    expect(service.currency()).toBeNull();
  });

  it('keys the unauthenticated case consistently', () => {
    expect(householdSessionKey()).toBe('no-session');
    session('hh-1', 't');
    expect(householdSessionKey()).toBe('hh-1:t');
  });
});
