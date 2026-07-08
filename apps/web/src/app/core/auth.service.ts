import { Injectable, computed, inject } from '@angular/core';
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
    });
    return { ok: true };
  }

  logout(): void {
    clearAuthState();
  }
}
