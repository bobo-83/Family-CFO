import { Injectable } from '@angular/core';
import {
  createAuthSession,
  createGoal,
  createPairingSession,
  getAiRuntimeConfig,
  getHouseholdContext,
  listAccounts,
  listGoals,
  listPairedDevices,
  revokePairedDevice,
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

  createPairingSession() {
    return createPairingSession();
  }

  listPairedDevices() {
    return listPairedDevices();
  }

  revokePairedDevice(deviceId: string) {
    return revokePairedDevice({ path: { device_id: deviceId } });
  }
}
