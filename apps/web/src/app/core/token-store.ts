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

export function setAuthState(state: StoredAuthState): void {
  authState.set(state);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function clearAuthState(): void {
  authState.set(null);
  localStorage.removeItem(STORAGE_KEY);
}

export function getToken(): string | null {
  return authState()?.accessToken ?? null;
}
