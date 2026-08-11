import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';

/**
 * #97: a member retires their own password.
 *
 * The current password is asked for even though the visitor obviously has a
 * session — a session can be an unattended open laptop, and this is the one
 * screen where that distinction matters.
 *
 * On success the server signs out every OTHER session of this member and keeps
 * this one, so the page stays usable and says so rather than bouncing the
 * member to the login screen.
 */
@Component({
  selector: 'app-change-password',
  imports: [
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
  ],
  templateUrl: './change-password.html',
  styleUrl: './change-password.scss',
})
export class ChangePassword {
  private readonly api = inject(ApiService);
  private readonly formBuilder = inject(FormBuilder);

  // minLength(8) matches the invite flow and the server's schema — one bar for
  // setting a password, not a third one invented here.
  protected readonly form = this.formBuilder.nonNullable.group({
    currentPassword: ['', [Validators.required]],
    newPassword: ['', [Validators.required, Validators.minLength(8)]],
    confirmPassword: ['', [Validators.required]],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);
  protected readonly changed = signal(false);

  /** Client-side only: the server has no opinion about a confirmation field. */
  protected get mismatched(): boolean {
    const { newPassword, confirmPassword } = this.form.getRawValue();
    return confirmPassword.length > 0 && newPassword !== confirmPassword;
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.mismatched || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.submitError.set(null);
    this.changed.set(false);

    const { currentPassword, newPassword } = this.form.getRawValue();
    const { error, response } = await this.api.changePassword({
      current_password: currentPassword,
      new_password: newPassword,
    });
    this.submitting.set(false);

    // A 204 carries no body, so there is no `data` to test for success the way
    // the other forms do — the status is the answer. `response` is optional in
    // the client's envelope; a missing one is treated as a failure, which is
    // the safe direction for a security action.
    if (error || !response?.ok) {
      // `response` is passed on purpose (#92): a 429 here carries Retry-After,
      // and the lockout is minutes long — the message must name the wait
      // instead of guessing.
      this.submitError.set(
        apiErrorMessage(
          error,
          $localize`:Change password error|Fallback when the server gives no reason:Could not change your password. Try again.`,
          response,
        ),
      );
      return;
    }

    this.form.reset();
    this.changed.set(true);
  }
}
