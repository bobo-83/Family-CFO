import { vi } from 'vitest';
import {
  authState,
  clearAuthState,
  getToken,
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
      accessToken: 'token-123',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });

    expect(getToken()).toBe('token-123');
    expect(authState()?.role).toBe('owner');
    expect(localStorage.getItem('family-cfo-auth')).toContain('token-123');
  });

  it('notifies auth replacements and logout synchronously until unsubscribed', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAuthState(listener);
    const a = {
      accessToken: 'token-a',
      householdId: 'household-a',
      userId: 'user-a',
      role: 'owner',
    };
    const b = {
      accessToken: 'token-b',
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
      accessToken: 'token-123',
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
      accessToken: 'token-123',
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
      accessToken: 'token-123',
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
      accessToken: 'token-123',
      householdId: 'household-1',
      userId: 'user-1',
      role: 'owner',
    });

    clearAuthState();

    expect(authState()).toBeNull();
    expect(getToken()).toBeNull();
    expect(localStorage.getItem('family-cfo-auth')).toBeNull();
  });
});
