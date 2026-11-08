import { vi } from 'vitest';
import {
  authGeneration,
  authState,
  clearAuthState,
  getToken,
  noteHouseholdReadable,
  sessionFence,
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

  it('stores and reads back the auth state', () => {
    setAuthState({
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

  it('notifies auth replacements and logout synchronously until unsubscribed', () => {
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

    setAuthState(a);
    expect(listener).toHaveBeenLastCalledWith(a);
    setAuthState(b);
    expect(listener).toHaveBeenLastCalledWith(b);
    clearAuthState();
    expect(listener).toHaveBeenLastCalledWith(null);

    unsubscribe();
    setAuthState(a);
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('continues notifying listeners after one listener throws', () => {
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

    expect(() => setAuthState(state)).not.toThrow();
    expect(listener).toHaveBeenCalledWith(state);

    unsubscribeFailing();
    unsubscribe();
  });

  it('updates auth and notifies when localStorage setItem throws', () => {
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
      expect(() => setAuthState(state)).not.toThrow();
      expect(authState()).toEqual(state);
      expect(listener).toHaveBeenCalledWith(state);
    } finally {
      setItem.mockRestore();
      unsubscribe();
    }
  });

  it('clears auth and notifies when localStorage removeItem throws', () => {
    setAuthState({
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

  it('clears the auth state and localStorage', () => {
    setAuthState({
      accessToken: 'token-123', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });

    const owner = localStorage.getItem('family-cfo-auth-owner');
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

  it('expires on the monotonic deadline even after a wall clock rollback', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance'] });
    setAuthState({
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

  it('fails closed for malformed expiry without reporting expiry', () => {
    expect(setAuthState({
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

it('does not erase a newer shared session before its storage event arrives', () => {
  try {
    setAuthState({
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

describe('shared-pointer fencing (#164 review)', () => {
  afterEach(() => {
    clearAuthState();
    localStorage.clear();
  });

  it('refuses an install whose pointer moved while the request was in flight', () => {
    const fence = sessionFence();
    const newerOwner = '00000000000000000000000000000164';
    localStorage.setItem('family-cfo-auth-session:' + newerOwner, JSON.stringify({
      accessToken: 'newer-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'newer-household', userId: 'newer-user', role: 'owner',
    }));
    localStorage.setItem('family-cfo-auth-owner', newerOwner);

    expect(setAuthState({
      accessToken: 'stale-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'stale-household', userId: 'stale-user', role: 'owner',
    }, fence)).toBe(false);

    expect(authState()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth-owner')).toBe(newerOwner);
    expect(localStorage.getItem('family-cfo-auth-session:' + newerOwner)).toContain('newer-bearer');
  });

  it('installs when the shared pointer never moved', () => {
    const fence = sessionFence();
    expect(setAuthState({
      accessToken: 'only-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'household', userId: 'member', role: 'owner',
    }, fence)).toBe(true);
    expect(getToken()).toBe('only-bearer');
  });

  it('fences open to a memory-only install when storage is unreadable', () => {
    const broken = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    try {
      expect(sessionFence()).toEqual({ pointer: null });
      expect(setAuthState({
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

  it('clears the session when another tab removes this tab\'s record', () => {
    setAuthState({
      accessToken: 'mine-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    const pointer = localStorage.getItem('family-cfo-auth-owner');
    localStorage.removeItem('family-cfo-auth-session:' + pointer);

    fireStorage('family-cfo-auth-session:' + pointer);

    expect(authState()).toBeNull();
    expect(signInReason()).toBeNull();
  });

  it('treats a pointer event that still names this tab\'s owner as no change', () => {
    setAuthState({
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

  it('installs another tab\'s session and discards this tab\'s superseded record', () => {
    setAuthState({
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

  it('updateSessionRights refreshes only the live owner and keeps the generation', () => {
    setAuthState({
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

  it('noteHouseholdReadable ignores a stale owner', () => {
    setAuthState({
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

  it('surfaces the expired reason when a signed-out install meets a past deadline', () => {
    expect(setAuthState({
      accessToken: 'past-bearer', expiresAt: '2000-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    })).toBe(false);
    expect(signInReason()).toBe('expired');
  });

  it('leaves a live session and its reason untouched when a later install is already expired', () => {
    setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    expect(setAuthState({
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

  it('expires the session with the expired reason when its record vanishes after the deadline', () => {
    const t0 = Date.now();
    setAuthState({
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

  it('ignores storage events for keys outside the session namespace', () => {
    setAuthState({
      accessToken: 'live-bearer', expiresAt: '2099-01-01T00:00:00Z',
      householdId: 'hh', userId: 'member', role: 'owner',
    });
    fireStorage('some-other-app:key');
    expect(getToken()).toBe('live-bearer');
  });
});
