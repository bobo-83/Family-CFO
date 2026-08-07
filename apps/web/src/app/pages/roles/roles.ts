import { Component, inject, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import type { Role } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';

/**
 * Human names for the rights catalog (apps/api/.../rights.py). The keys are the
 * identifiers the server enforces and must never be translated.
 */
const RIGHT_LABELS: Record<string, string> = {
  'finances.view': $localize`:Right label|Permission to see the household's money:Finances — view`,
  'advisor.use': $localize`:Right label|Permission to chat with the advisor:Advisor — use`,
  'advisor.manage': $localize`:Right label|Permission to curate advisor memory and conversations:Advisor — manage`,
  'transactions.manage': $localize`:Right label|Permission to edit transactions:Transactions — manage`,
  'bills.manage': $localize`:Right label|Permission to edit bills:Bills — manage`,
  'budgets.manage': $localize`:Right label|Permission to edit budgets:Budgets — manage`,
  'goals.manage': $localize`:Right label|Permission to edit savings goals:Goals — manage`,
  'categories.manage': $localize`:Right label|Permission to edit spending categories:Categories — manage`,
  'income.manage': $localize`:Right label|Permission to edit income:Income — manage`,
  'imports.manage': $localize`:Right label|Permission to import statements:Imports — manage`,
  'reports.manage': $localize`:Right label|Permission to generate reports:Reports — manage`,
  'accounts.manage': $localize`:Right label|Permission to edit accounts:Accounts — manage`,
  'connections.manage': $localize`:Right label|Permission to link and sync banks:Connections — manage`,
  'members.manage': $localize`:Right label|Permission to edit household members:Members — manage`,
  'roles.manage': $localize`:Right label|Permission to edit roles:Roles — manage`,
  'devices.manage': $localize`:Right label|Permission to pair and revoke devices:Devices — manage`,
  'backups.manage': $localize`:Right label|Permission to run and restore backups:Backups — manage`,
  'audit.view': $localize`:Right label|Permission to read the audit log:Audit — view`,
  'household.settings.manage': $localize`:Right label|Permission to change household settings:Household — settings`,
  'ai_runtime.manage': $localize`:Right label|Permission to control the local AI runtime:Ai runtime — manage`,
  'system.admin': $localize`:Right label|Box-level administrator:System — admin`,
};

/** Fallback for a right the catalog above hasn't caught up with. */
function derivedRightLabel(right: string): string {
  const [area = right, verb = ''] = right.split('.');
  const pretty = area.replace(/_/g, ' ');
  return `${pretty.charAt(0).toUpperCase()}${pretty.slice(1)} — ${verb}`;
}

/**
 * Household roles (ADR 0034): the built-in presets plus custom roles the
 * household defines by ticking rights. Admin is immutable; a role that's still
 * assigned can't be deleted.
 */
@Component({
  selector: 'app-roles',
  imports: [
    FormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatCheckboxModule,
    MatButtonModule,
  ],
  templateUrl: './roles.html',
  styleUrl: './roles.scss',
})
export class Roles {
  private readonly api = inject(ApiService);

  protected readonly data = resource({
    loader: async () => {
      const { data, error } = await this.api.listRoles();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load roles.`));
      }
      return data;
    },
  });

  protected readonly actionError = signal<string | null>(null);
  protected readonly saving = signal(false);

  // The editor: null = closed; a Role without id = creating a new one.
  protected readonly editing = signal<{ id: string | null; name: string; rights: Set<string> } | null>(null);

  protected startCreate(): void {
    this.actionError.set(null);
    this.editing.set({ id: null, name: '', rights: new Set(['finances.view']) });
  }

  protected startEdit(role: Role): void {
    if (role.built_in) {
      return;
    }
    this.actionError.set(null);
    this.editing.set({ id: role.id, name: role.name, rights: new Set(role.rights) });
  }

  protected toggleRight(right: string): void {
    const current = this.editing();
    if (!current) {
      return;
    }
    if (current.rights.has(right)) {
      current.rights.delete(right);
    } else {
      current.rights.add(right);
    }
    this.editing.set({ ...current });
  }

  protected async save(): Promise<void> {
    const current = this.editing();
    if (!current || !current.name.trim() || this.saving()) {
      return;
    }
    this.saving.set(true);
    this.actionError.set(null);
    const body = { name: current.name.trim(), rights: [...current.rights].sort() };
    const result = current.id
      ? await this.api.updateRole(current.id, body)
      : await this.api.createRole(body);
    this.saving.set(false);
    if (result.error) {
      this.actionError.set(apiErrorMessage(result.error, $localize`Failed to save the role.`));
      return;
    }
    this.editing.set(null);
    this.data.reload();
  }

  protected async remove(role: Role): Promise<void> {
    if (role.built_in || (role.member_count ?? 0) > 0) {
      return;
    }
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before a custom role is deleted:Delete the role "${role.name}:name:"?`,
      )
    ) {
      return;
    }
    const { error } = await this.api.deleteRole(role.id);
    if (error) {
      this.actionError.set(apiErrorMessage(error, $localize`Failed to delete the role.`));
      return;
    }
    this.data.reload();
  }

  /** Rights of a role, sorted so the drill-down groups by area (accounts.*, …). */
  protected sortedRights(role: Role): string[] {
    return [...role.rights].sort();
  }

  /**
   * "accounts.manage" -> "Accounts — manage" for scannable checkboxes. The
   * identifier itself is what the server matches on and is shown verbatim
   * beside the label; only this human name is translated. A right the catalog
   * hasn't caught up with falls back to the mechanical split.
   */
  protected label(right: string): string {
    return RIGHT_LABELS[right] ?? derivedRightLabel(right);
  }

  /** Members holding this role — the API omits the count on older responses. */
  protected memberCount(role: Role): number {
    return role.member_count ?? 0;
  }

  /** Why Delete is disabled, as the button's tooltip; empty when it is enabled. */
  protected deleteBlockedHint(role: Role): string {
    return this.memberCount(role) > 0
      ? $localize`:Tooltip|Why a role that is still assigned cannot be deleted:Reassign its members first`
      : '';
  }
}
