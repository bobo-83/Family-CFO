import { Injectable } from '@angular/core';
import {
  createAuthSession,
  createGoal,
  getAiRuntimeConfig,
  getHouseholdContext,
  listAccounts,
  listGoals,
  updateAiRuntimeConfig,
  type AiRuntimeConfig,
  type AuthSessionCreateRequest,
  type GoalCreateRequest,
} from '../api-client';

/**
 * A thin wrapper around the generated client's SDK functions.
 *
 * Angular's Vitest test runner does not support `vi.mock()` on relative
 * imports ("Please use Angular TestBed for mocking dependencies"), so
 * components depend on this injectable service instead of importing
 * generated functions directly — tests substitute it via DI.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  login(body: AuthSessionCreateRequest) {
    return createAuthSession({ body });
  }

  getHouseholdContext() {
    return getHouseholdContext();
  }

  listAccounts() {
    return listAccounts();
  }

  listGoals() {
    return listGoals();
  }

  createGoal(body: GoalCreateRequest) {
    return createGoal({ body });
  }

  getAiRuntimeConfig() {
    return getAiRuntimeConfig();
  }

  updateAiRuntimeConfig(body: AiRuntimeConfig) {
    return updateAiRuntimeConfig({ body });
  }
}
