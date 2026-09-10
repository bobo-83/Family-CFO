import { signal } from '@angular/core';

const STORAGE_KEY = 'family-cfo-auth';

export interface StoredAuthState {
  accessToken: string;
  householdId: string;
  userId: string;
  role: string;
  // ADR 0034: the assigned role's name and resolved rights; screens gate with
  // these. Older stored sessions may lack them (fallback maps from `role`).
  roleName?: string;
  rights?: string[];
}

function loadInitialState(): StoredAuthState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredAuthState) : null;
  } catch {
    return null;
  }
}

/**
 * Plain module-level signal (not an Angular service) so the generated
 * API client's fetch interceptor — which lives outside Angular's DI
 * graph — can read the current token without an injection context.
 */
export const authState = signal<StoredAuthState | null>(loadInitialState());

type AuthStateListener = (state: StoredAuthState | null) => void;
const authStateListeners = new Set<AuthStateListener>();

/** Subscribe to synchronous login, replacement, and logout transitions. */
export function subscribeToAuthState(listener: AuthStateListener): () => void {
  authStateListeners.add(listener);
  return () => authStateListeners.delete(listener);
}

function notifyAuthStateListeners(state: StoredAuthState | null): void {
  for (const listener of [...authStateListeners]) {
    try {
      listener(state);
    } catch {
      // Auth transitions must reach every listener even if one listener fails.
    }
  }
}

function persistAuthState(state: StoredAuthState | null): void {
  try {
    if (state) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage is only a reload cache. Its failure must not block the live auth
    // transition or synchronous session-owner invalidation.
  }
}

export function setAuthState(state: StoredAuthState): void {
  authState.set(state);
  persistAuthState(state);
  notifyAuthStateListeners(state);
}

export function clearAuthState(): void {
  authState.set(null);
  persistAuthState(null);
  notifyAuthStateListeners(null);
}

export function getToken(): string | null {
  return authState()?.accessToken ?? null;
}

/**
 * One-shot message for the login page explaining why the session ended —
 * e.g. a 423 "household sealed and locked" answer (ADR 0072 Phase 3). Module
 * level for the same reason as `authState`: the fetch interceptor that sets
 * it runs outside Angular's DI graph.
 */
