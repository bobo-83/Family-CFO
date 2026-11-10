import { vi } from 'vitest';
import {
  authGeneration,
  authState,
  clearAuthState,
  getToken,
  noteHouseholdReadable,
  sessionFence,
  sharedSessionMoved,
  setAuthState,
  signInReason,
  subscribeToAuthState,
  updateSessionRights,
} from './token-store';

describe('token-store', () => {
  beforeEach(() => {
    localStorage.clear();
    clearAuthState();
  });

  it('starts with no auth state', () => {
    expect(authState()).toBeNull();
    expect(getToken()).toBeNull();
  });

  it('stores and reads back the auth state', async () => {
    await setAuthState({
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });

    expect(getToken()).toBe('token-123');
    expect(authState()?.role).toBe('owner');
    const owner = localStorage.getItem('family-cfo-auth-owner');
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toContain('token-123');
  });

  it('notifies auth replacements and logout synchronously until unsubscribed', async () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAuthState(listener);
    const a = {
      accessToken: 'token-a', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-a',
      userId: 'user-a',
      role: 'owner',
    };
    const b = {
      accessToken: 'token-b', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-b',
      userId: 'user-b',
      role: 'owner',
    };

    await setAuthState(a);
    expect(listener).toHaveBeenLastCalledWith(a);
    await setAuthState(b);
    expect(listener).toHaveBeenLastCalledWith(b);
    clearAuthState();
    expect(listener).toHaveBeenLastCalledWith(null);

    unsubscribe();
    await setAuthState(a);
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('continues notifying listeners after one listener throws', async () => {
    const unsubscribeFailing = subscribeToAuthState(() => {
      throw new Error('listener failed');
    });
    const listener = vi.fn();
    const unsubscribe = subscribeToAuthState(listener);
    const state = {
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    };

    // The install resolves: a throwing listener must not reject it.
    await expect(setAuthState(state)).resolves.toBe(true);
    expect(listener).toHaveBeenCalledWith(state);

    unsubscribeFailing();
    unsubscribe();
  });

  it('updates auth and notifies when localStorage setItem throws', async () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAuthState(listener);
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    const state = {
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    };

    try {
      // Storage is unavailable: the memory install still happens.
      await expect(setAuthState(state)).resolves.toBe(true);
      expect(authState()).toEqual(state);
      expect(listener).toHaveBeenCalledWith(state);
    } finally {
      setItem.mockRestore();
      unsubscribe();
    }
  });

  it('clears auth and notifies when localStorage removeItem throws', async () => {
    await setAuthState({
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });
    const listener = vi.fn();
    const unsubscribe = subscribeToAuthState(listener);
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });

    try {
      expect(() => clearAuthState()).not.toThrow();
      expect(authState()).toBeNull();
      expect(getToken()).toBeNull();
      expect(listener).toHaveBeenCalledWith(null);
    } finally {
      removeItem.mockRestore();
      unsubscribe();
    }
  });

  it('clears the auth state and localStorage', async () => {
    await setAuthState({
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });

    const owner = localStorage.getItem('family-cfo-auth-owner');
    // The sign-out clears the memory and the shared record in one step, so a
    // reconciliation that lands right after it finds nothing to reinstall.
    clearAuthState();

    expect(authState()).toBeNull();
    expect(getToken()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
  });
});

describe('session deadlines', () => {
  afterEach(() => {
    clearAuthState();
    vi.useRealTimers();
  });

  it('expires on the monotonic deadline even after a wall clock rollback', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance'] });
    await setAuthState({
      accessToken: 'synthetic-bearer',
      expiresAt: new Date(Date.now() + 1000).toISOString(),
      householdId: 'hh',
      userId: 'member',
      role: 'owner',
    });
    vi.setSystemTime(Date.now() - 60_000);
    vi.advanceTimersByTime(1000);
    expect(authState()).toBeNull();
  });

  it('fails closed for malformed expiry without reporting expiry', async () => {
    expect(await setAuthState({
      accessToken: 'synthetic-bearer',
      expiresAt: 'broken',
      householdId: 'hh',
      userId: 'member',
      role: 'owner',
    })).toBe(false);
    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
  });
});

it('does not erase a newer shared session before its storage event arrives', async () => {
  try {
    await setAuthState({
      accessToken: 'outgoing-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'a', userId: 'old-member', role: 'adult',
    });
    const newOwner = '00000000000000000000000000000164';
    const recordKey = 'family-cfo-auth-session:' + newOwner;
    localStorage.setItem(recordKey, JSON.stringify({
      accessToken: 'incoming-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'b', userId: 'new-member', role: 'owner',
    }));
    localStorage.setItem('family-cfo-auth-owner', newOwner);

    clearAuthState('rejected');

    expect(localStorage.getItem(recordKey)).toContain('incoming-bearer');
    window.dispatchEvent(new StorageEvent('storage', { key: 'family-cfo-auth-owner' }));
    expect(authState()?.accessToken).toBe('incoming-bearer');
  } finally {
    clearAuthState();
    localStorage.clear();
  }
});

describe('shared-session comparison (#164 review)', () => {
  afterEach(() => {
    clearAuthState();
    localStorage.clear();
  });

  it('reports no move when there was no fence to compare against', () => {
    // No fence means no shared session was pinned down, so there is nothing
    // to have moved.
    expect(sharedSessionMoved(null)).toBe(false);
  });

  it('reports no move when the shared session cannot be read', async () => {
    const fence = sessionFence();
    const broken = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    try {
      expect(sharedSessionMoved(fence)).toBe(false);
    } finally {
      broken.mockRestore();
    }
  });

  it('reports a move when the shared session changed', async () => {
    const fence = sessionFence();
    expect(sharedSessionMoved(fence)).toBe(false);

    await setAuthState({
      accessToken: 'moved-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    });

    expect(sharedSessionMoved(fence)).toBe(true);
  });
});

describe('shared-pointer fencing (#164 review)', () => {
  afterEach(() => {
    clearAuthState();
    localStorage.clear();
  });

  /**
   * Run `body` with a stand-in for the Web Locks API, so the cross-tab
   * section can be driven from a test. The stand-in is what another tab's
   * lock-protected write looks like from the inside.
   */
  async function withFakeLocks(
    locks: { request: <R>(name: string, callback: () => R | Promise<R>) => Promise<R> },
    body: () => Promise<void>,
  ): Promise<void> {
    const scope = globalThis.navigator as unknown as { locks?: unknown };
    const previous = Object.getOwnPropertyDescriptor(scope, 'locks');
    Object.defineProperty(scope, 'locks', { value: locks, configurable: true });
    try {
      await body();
    } finally {
      if (previous) Object.defineProperty(scope, 'locks', previous);
      else delete scope.locks;
    }
  }

  it('refuses an install whose pointer moved while the request was in flight', async () => {
    const fence = sessionFence();
    const newerOwner = '00000000000000000000000000000164';
    localStorage.setItem('family-cfo-auth-session:' + newerOwner, JSON.stringify({
      accessToken: 'newer-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'newer-household', userId: 'newer-user', role: 'owner',
    }));
    localStorage.setItem('family-cfo-auth-owner', newerOwner);

    expect(await setAuthState({
      accessToken: 'stale-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'stale-household', userId: 'stale-user', role: 'owner',
    }, fence)).toBe(false);

    expect(authState()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(newerOwner);
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
  });

  it('does not reinstall a signed-out session when a focus lands right after (#164 review)', async () => {
    // A sign-out that waits for the cross-tab lock leaves a window: a focus,
    // pageshow or visibility reconciliation landing in it reads the outgoing
    // record and reinstalls the session the sign-out just ended, and the later
    // removal emits no storage event to this tab — so the tab stays
    // authenticated after signing out. Holding the lock makes that window
    // observable: anything that waits for it cannot land.
    await setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    });
    const owner = localStorage.getItem('family-cfo-auth-owner');
    const locks = {
      request: async <R>(_name: string, callback: () => R | Promise<R>): Promise<R> => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        return await callback();
      },
    };

    await withFakeLocks(locks, async () => {
      clearAuthState();
      // A reconciliation lands right after the sign-out.
      window.dispatchEvent(new Event('focus'));

      expect(authState()).toBeNull();
      expect(getToken()).toBeNull();
      expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
    });
  });

  it('installs when the shared pointer never moved', async () => {
    const fence = sessionFence();
    expect(await setAuthState({
      accessToken: 'only-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    }, fence)).toBe(true);
    expect(getToken()).toBe('only-bearer');
  });

  it('compares the shared session under the cross-tab lock (#164 review)', async () => {
    // Another tab held the lock first and published a newer session while this
    // tab waited for it. The comparison runs inside the lock, so it sees that
    // session and refuses the stale install — a fence compared before the
    // lock would have read the older one and installed over it.
    const newerOwner = '00000000000000000000000000000164';
    const fence = sessionFence();
    const requests: string[] = [];
    const locks = {
      request: async <R>(_name: string, callback: () => R | Promise<R>): Promise<R> => {
        requests.push(_name);
        localStorage.setItem('family-cfo-auth-session:' + newerOwner, JSON.stringify({
          accessToken: 'newer-bearer', expiresAt: '2099-01-01T00:00:00Z',
          householdId: 'newer-household', userId: 'newer-user', role: 'owner',
        }));
        localStorage.setItem('family-cfo-auth-owner', newerOwner);
        return await callback();
      },
    };

    await withFakeLocks(locks, async () => {
      expect(await setAuthState({
        accessToken: 'stale-bearer', expiresAt: '2099-01-01T00:00:00Z',
        householdId: 'stale-household', userId: 'stale-user', role: 'owner',
      }, fence)).toBe(false);
    });

    // The lock was taken: without it the comparison would have read the
    // older session and installed over the newer one.
    expect(requests.length).toBeGreaterThan(0);
    expect(authState()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(newerOwner);
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
  });

  it('refuses a pending install when another tab signed out in the meantime (#164 review)', async () => {
    await setAuthState({
      accessToken: 'first-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    });
    const fence = sessionFence();
    const owner = localStorage.getItem('family-cfo-auth-owner');
    expect(fence?.pointer).toBe(owner);

    // Another tab signs out: the record goes, the pointer stays (the restore
    // path reads it), so only a fence that looks at the record can see it.
    localStorage.removeItem('family-cfo-auth-session:' + owner);

    expect(await setAuthState({
      accessToken: 'stale-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    }, fence)).toBe(false);

    // The sign-out stands: the refused install left no record of its own.
    expect(localStorage.length).toBe(1);
    expect(localStorage.key(0)).toBe('family-cfo-auth-owner');
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(owner);
  });

  it('fences open to a memory-only install when storage is unreadable', async () => {
    const broken = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    try {
      // Storage cannot be read, so there is no shared session to fence on:
      // the fence is open and the install runs in memory only.
      expect(sessionFence()).toBeNull();
      expect(await setAuthState({
        accessToken: 'offline-bearer', expiresAt: '2099-01-01T00:00:00Z',
        householdId: 'household', userId: 'member', role: 'owner',
      }, sessionFence())).toBe(true);
      expect(getToken()).toBe('offline-bearer');
    } finally {
      broken.mockRestore();
    }
  });
});

function fireStorage(key: string | null): void {
  window.dispatchEvent(new StorageEvent('storage', { key }));
}

function sessionRecord(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    accessToken: 'restored-bearer', expiresAt: '2099-01-01T00:00:00Z',
    householdId: 'restored-household', userId: 'restored-user', role: 'owner',
    ...overrides,
  });
}

describe('shared-session reconciliation (#164)', () => {
  afterEach(() => {
    clearAuthState();
    signInReason.set(null);
    localStorage.clear();
  });

  it('drops an expired stored session surfaced by a storage event, with the expiry reason', () => {
    const owner = '00000000000000000000000000000164';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, sessionRecord({ expiresAt: '2000-01-01T00:00:00Z' }));

    fireStorage('family-cfo-auth-owner');

    expect(authState()).toBeNull();
    expect(signInReason()).toBe('expired');
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
  });

  it('rejects a malformed owner pointer', () => {
    localStorage.setItem('family-cfo-auth-owner', 'not-an-owner-id');
    fireStorage('family-cfo-auth-owner');
    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
  });

  it('clears the session when another tab removes this tab\'s record', async () => {
    await setAuthState({
      accessToken: 'mine-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    const pointer = localStorage.getItem('family-cfo-auth-owner');
    localStorage.removeItem('family-cfo-auth-session:' + pointer);

    fireStorage('family-cfo-auth-session:' + pointer);

    expect(authState()).toBeNull();
    expect(signInReason()).toBeNull();
  });

  it('treats a pointer event that still names this tab\'s owner as no change', async () => {
    await setAuthState({
      accessToken: 'mine-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    fireStorage('family-cfo-auth-owner');
    expect(getToken()).toBe('mine-bearer');
  });

  it('rejects a corrupt stored record and removes it', () => {
    const owner = '00000000000000000000000000000165';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, '{corrupt');
    fireStorage('family-cfo-auth-owner');
    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
  });

  it('rejects a stored record that parses but lacks required fields', () => {
    const owner = '00000000000000000000000000000166';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, JSON.stringify({ accessToken: 'half' }));
    fireStorage('family-cfo-auth-owner');
    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
  });

  it('installs another tab\'s session and discards this tab\'s superseded record', async () => {
    await setAuthState({
      accessToken: 'old-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'old', userId: 'old-user', role: 'owner',
    });
    const mine = localStorage.getItem('family-cfo-auth-owner');
    const newer = '00000000000000000000000000000167';
    localStorage.setItem('family-cfo-auth-session:' + newer, sessionRecord());
    localStorage.setItem('family-cfo-auth-owner', newer);

    fireStorage('family-cfo-auth-owner');

    expect(getToken()).toBe('restored-bearer');
    expect(localStorage.getItem('family-cfo-auth-session:' + mine)).toBeNull();
  });

  it('survives storage throwing mid-reconciliation', () => {
    const broken = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    try {
      fireStorage('family-cfo-auth-owner');
      expect(authState()).toBeNull();
    } finally {
      broken.mockRestore();
    }
  });

  it('reconciles on window focus, adopting a session another tab installed', () => {
    const newer = '00000000000000000000000000000168';
    localStorage.setItem('family-cfo-auth-session:' + newer, sessionRecord());
    localStorage.setItem('family-cfo-auth-owner', newer);
    window.dispatchEvent(new Event('focus'));
    expect(getToken()).toBe('restored-bearer');
  });

  it('updateSessionRights refreshes only the live owner and keeps the generation', async () => {
    await setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'adult',
    });
    const generation = authGeneration();
    updateSessionRights(generation, { role: 'owner', roleName: 'Owner', rights: ['system.admin'] });
    expect(authState()?.role).toBe('owner');
    expect(authState()?.rights).toEqual(['system.admin']);
    expect(authGeneration()).toBe(generation);
    updateSessionRights(generation + 1, { role: 'member' });
    expect(authState()?.role).toBe('owner');
  });

  it('noteHouseholdReadable ignores a stale owner', async () => {
    await setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    noteHouseholdReadable(authGeneration() + 7);
    expect(authState()).not.toBeNull();
  });
});

describe('legacy first load (#164)', () => {
  it('drops a pre-expiry stored session on first load, requiring one sign-in', async () => {
    localStorage.setItem('family-cfo-auth', JSON.stringify({
      accessToken: 'legacy-bearer', householdId: 'hh', userId: 'member', role: 'owner',
    }));
    vi.resetModules();
    const fresh = await import('./token-store');
    expect(localStorage.getItem('family-cfo-auth')).toBeNull();
    expect(fresh.signInReason()).toBe('rejected');
    expect(fresh.authState()).toBeNull();
    localStorage.clear();
  });
});

describe('branch-closing expiry paths (#164)', () => {
  afterEach(() => {
    clearAuthState();
    signInReason.set(null);
    localStorage.clear();
  });

  it('rejects a restored record whose expiry is not parseable, as if unsigned', () => {
    const owner = '00000000000000000000000000000170';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, sessionRecord({ expiresAt: 'soon' }));

    fireStorage('family-cfo-auth-owner');

    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
  });

  it('surfaces the expired reason when a signed-out install meets a past deadline', async () => {
    expect(await setAuthState({
      accessToken: 'past-bearer', expiresAt: '2000-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    })).toBe(false);
    expect(signInReason()).toBe('expired');
  });

  it('leaves a live session and its reason untouched when a later install is already expired', async () => {
    await setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    expect(await setAuthState({
      accessToken: 'late-bearer', expiresAt: '2000-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    })).toBe(false);
    expect(getToken()).toBe('live-bearer');
    expect(signInReason()).toBeNull();
  });

  it('labels a record with non-list rights as corrupt and removes it', () => {
    const owner = '00000000000000000000000000000171';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, sessionRecord({ rights: 'owner' }));

    fireStorage('family-cfo-auth-owner');

    expect(authState()).toBeNull();
    expect(signInReason()).toBe('rejected');
    expect(localStorage.getItem('family-cfo-auth-session:' + owner)).toBeNull();
  });

  it('expires the session with the expired reason when its record vanishes after the deadline', async () => {
    const t0 = Date.now();
    await setAuthState({
      accessToken: 'short-bearer', expiresAt: new Date(t0 + 30).toISOString(),
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    const pointer = localStorage.getItem('family-cfo-auth-owner');
    localStorage.removeItem('family-cfo-auth-session:' + pointer);
    while (Date.now() < t0 + 40) { /* past the deadline the clock check would have fired */ }

    fireStorage('family-cfo-auth-session:' + pointer);

    expect(authState()).toBeNull();
    expect(signInReason()).toBe('expired');
  });

  it('ignores storage events for keys outside the session namespace', async () => {
    await setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    fireStorage('some-other-app:key');
    expect(getToken()).toBe('live-bearer');
  });
});
