import { Injectable, computed, inject } from '@angular/core';
import type { HouseholdCreateRequest } from '../api-client';
import { apiErrorMessage } from '../shared/api-error';
import { ApiService } from './api.service';
import { authState, clearAuthState, setAuthState } from './token-store';

export interface LoginResult {
  ok: boolean;
  errorMessage?: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);

  readonly isAuthenticated = computed(() => authState() !== null);
  readonly role = computed(() => authState()?.role ?? null);
  readonly householdId = computed(() => authState()?.householdId ?? null);
  readonly roleName = computed(() => authState()?.roleName ?? null);

  /** ADR 0034: gate screens/sections by RIGHT, never by role name. A stored
   *  session from before rights shipped falls back to its legacy tier. */
  readonly hasRight = (right: string): boolean => {
    const state = authState();
    if (!state) {
      return false;
    }
    if (state.rights) {
      return state.rights.includes(right);
    }
    // Legacy fallback: owner had everything; adult had the money-editing set.
    if (state.role === 'owner') {
      return true;
    }
    if (state.role === 'adult') {
      return ![
        'accounts.manage', 'imports.manage', 'connections.manage', 'reports.manage',
        'members.manage', 'roles.manage', 'devices.manage', 'backups.manage',
        'audit.view', 'household.settings.manage', 'ai_runtime.manage',
      ].includes(right);
    }
    return ['finances.view', 'advisor.use'].includes(right);
  };

  async login(email: string, password: string): Promise<LoginResult> {
    const { data, error } = await this.api.login({ email, password });

    if (error || !data) {
      return { ok: false, errorMessage: apiErrorMessage(error, 'Login failed. Check your email and password.') };
    }

    setAuthState({
      accessToken: data.access_token,
      householdId: data.household_id,
      userId: data.user_id,
      role: data.role,
      roleName: data.role_name ?? undefined,
      rights: data.rights ?? undefined,
    });
    return { ok: true };
  }

  async signup(payload: HouseholdCreateRequest): Promise<LoginResult> {
    const { data, error } = await this.api.createHousehold(payload);

    if (error || !data) {
      return {
        ok: false,
        errorMessage: apiErrorMessage(error, 'Could not create the household. Try a different email.'),
      };
    }

    setAuthState({
      accessToken: data.access_token,
      householdId: data.household_id,
      userId: data.user_id,
      role: data.role,
      roleName: data.role_name ?? undefined,
      rights: data.rights ?? undefined,
    });
    return { ok: true };
  }

  logout(): void {
    clearAuthState();
  }
}
