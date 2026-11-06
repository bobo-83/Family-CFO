import { vi } from 'vitest';
import {
  authState,
  clearAuthState,
  getToken,
  signInReason,
  setAuthState,
  subscribeToAuthState,
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
