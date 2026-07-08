import { authState, clearAuthState, getToken, setAuthState } from './token-store';

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
