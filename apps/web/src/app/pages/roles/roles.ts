import { Component, inject, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import type { Role } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';

/**
 * Household roles (ADR 0034): the built-in presets plus custom roles the
 * household defines by ticking rights. Admin is immutable; a role that's still
 * assigned can't be deleted.
 */
@Component({
  selector: 'app-roles',
  imports: [FormsModule],
  templateUrl: './roles.html',
  styleUrl: './roles.scss',
})
export class Roles {
  private readonly api = inject(ApiService);

  protected readonly data = resource({
    loader: async () => {
      const { data, error } = await this.api.listRoles();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load roles.'));
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
      this.actionError.set(apiErrorMessage(result.error, 'Failed to save the role.'));
      return;
    }
    this.editing.set(null);
    this.data.reload();
  }

  protected async remove(role: Role): Promise<void> {
    if (role.built_in || (role.member_count ?? 0) > 0) {
      return;
    }
    if (!confirm(`Delete the role "${role.name}"?`)) {
      return;
    }
    const { error } = await this.api.deleteRole(role.id);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete the role.'));
      return;
    }
    this.data.reload();
  }

  /** "accounts.manage" -> "Accounts — manage" for scannable checkboxes. */
  protected label(right: string): string {
    const [area = right, verb = ''] = right.split('.');
    const pretty = area.replace(/_/g, ' ');
    return `${pretty.charAt(0).toUpperCase()}${pretty.slice(1)} — ${verb}`;
  }
}
