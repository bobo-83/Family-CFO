import { Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { authState, signInReason } from '../../core/token-store';

@Component({
  selector: 'app-login',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
  ],
  templateUrl: './login.html',
  styleUrl: './login.scss',
})
export class Login {
  private readonly destroyRef = inject(DestroyRef);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly form = this.formBuilder.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  protected readonly submitting = signal(false);
  protected readonly notice = computed(() => {
    switch (signInReason()) {
      case 'expired': return $localize`Your session has expired. Please sign in again.`;
      case 'rejected': return $localize`Please sign in again to continue.`;
      default: return null;
    }
  });

  /** The live session, if this tab has one. */
  protected readonly session = computed(() => authState());

  /**
   * This tab's own sign-in attempt. Its session is expected here, so it must
   * not be mistaken for another tab signing in.
   */
  private localAttempt = false;

  /** Whether a session was already there the last time this page looked. */
  private hadSession = false;

  constructor() {
    this.destroyRef.onDestroy(() => signInReason.set(null));
    effect(() => {
      const session = this.session();
      const appeared = session !== null && !this.hadSession;
      this.hadSession = session !== null;
      // Another tab signed in while this page was open. The live session is
      // what this page was standing in front of, so there is nothing left to
      // sign in to.
      if (appeared && !this.localAttempt && this.router.url === '/login') {
        void this.router.navigateByUrl('/overview', { replaceUrl: true });
      }
    });
  }

  protected readonly errorMessage = signal<string | null>(null);

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.errorMessage.set(null);
    // The session this attempt brings is expected, not another tab signing in.
    this.localAttempt = true;

    const { email, password } = this.form.getRawValue();
    const result = await this.auth.login(email, password);

    this.submitting.set(false);

    if (!result.ok) {
      // No session came out of it, so a later session is another tab's.
      this.localAttempt = false;
      this.errorMessage.set(result.errorMessage ?? $localize`Login failed.`);
      return;
    }

    if (!this.destroyRef.destroyed) await this.router.navigateByUrl('/overview');
  }
}
