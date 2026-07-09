import { Component, computed, effect, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { AiModelInfo, AiRuntimeConfig } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

const PROVIDERS: AiRuntimeConfig['provider'][] = ['vllm', 'ollama', 'llama_cpp', 'openai_compatible'];

/** Extra memory beyond the weights for KV cache / runtime overhead (ADR 0012). */
const HEADROOM = 1.15;

export type FitVerdict = 'fits' | 'tight' | 'no' | 'unknown';

@Component({
  selector: 'app-ai-runtime',
  imports: [ReactiveFormsModule],
  templateUrl: './ai-runtime.html',
  styleUrl: './ai-runtime.scss',
})
export class AiRuntime {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly providers = PROVIDERS;
  protected readonly isOwner = () => this.auth.role() === 'owner';

  protected readonly config = resource({
    loader: async () => {
      const { data, error } = await this.api.getAiRuntimeConfig();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load AI runtime configuration.'));
      }
      return data;
    },
  });

  protected readonly status = resource({
    loader: async () => {
      const { data } = await this.api.getAiRuntimeStatus();
      return data ?? null;
    },
  });

  protected readonly catalog = resource({
    loader: async () => {
      const { data, error } = await this.api.listAiModels();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load the model catalog.'));
      }
      return data.models;
    },
  });

  protected readonly hardware = resource({
    loader: async () => {
      const { data } = await this.api.getAiHardwareProfile();
      return data ?? null;
    },
  });

  // --- Model selection (REPLACES the served models; never additive) ---------

  protected readonly selectedMainId = signal<string | null>(null);
  protected readonly selectedVisionId = signal<string>('none');

  protected readonly mainOptions = computed(() =>
    (this.catalog.value() ?? []).filter((m) => m.role === 'main' || m.role === 'both'),
  );
  protected readonly visionOptions = computed(() =>
    (this.catalog.value() ?? []).filter((m) => m.role === 'vision'),
  );

  protected readonly selectedMain = computed<AiModelInfo | null>(
    () => this.mainOptions().find((m) => m.id === this.selectedMainId()) ?? null,
  );

  /** Vision model in play — hidden/ignored when the main model sees photos itself. */
  protected readonly selectedVision = computed<AiModelInfo | null>(() => {
    if (this.selectedVisionId() === 'none' || this.selectedMain()?.supports_vision) {
      return null;
    }
    return this.visionOptions().find((m) => m.id === this.selectedVisionId()) ?? null;
  });

  /** Live requirement for the SELECTED combination only (replacement semantics). */
  protected readonly requiredMemoryGb = computed(() => {
    const main = this.selectedMain();
    if (!main) {
      return null;
    }
    const vision = this.selectedVision();
    return Math.round((main.est_memory_gb + (vision?.est_memory_gb ?? 0)) * HEADROOM);
  });

  protected readonly requiredDiskGb = computed(() => {
    const main = this.selectedMain();
    if (!main) {
      return null;
    }
    return Math.round(main.est_disk_gb + (this.selectedVision()?.est_disk_gb ?? 0));
  });

  /** Memory budget: GPU memory when known, else system RAM (unified/unknown GPU). */
  protected readonly memoryBudgetGb = computed(() => {
    const hw = this.hardware.value();
    return hw?.gpu_memory_gb ?? hw?.system_memory_gb ?? null;
  });

  protected readonly usingSystemMemoryBudget = computed(() => {
    const hw = this.hardware.value();
    return hw != null && hw.gpu_memory_gb == null && hw.system_memory_gb != null;
  });

  protected readonly memoryVerdict = computed<FitVerdict>(() => {
    const required = this.requiredMemoryGb();
    const budget = this.memoryBudgetGb();
    if (required == null || budget == null) {
      return 'unknown';
    }
    if (required <= budget * 0.85) {
      return 'fits';
    }
    return required <= budget ? 'tight' : 'no';
  });

  protected readonly diskVerdict = computed<FitVerdict>(() => {
    const required = this.requiredDiskGb();
    const free = this.hardware.value()?.disk_free_gb ?? null;
    if (required == null || free == null) {
      return 'unknown';
    }
    // 1.5x covers download + extraction; already-cached weights aren't counted.
    if (required * 1.5 <= free) {
      return 'fits';
    }
    return required <= free ? 'tight' : 'no';
  });

  /** The exact operator command that applies the selection (ADR 0012). */
  protected readonly swapCommand = computed(() => {
    const main = this.selectedMain();
    if (!main) {
      return null;
    }
    if (main.supports_vision) {
      return `scripts/swap-model.sh ${main.id}`;
    }
    const vision = this.selectedVision();
    return `scripts/swap-model.sh ${main.id} ${vision ? vision.id : 'none'}`;
  });

  /** Selected model differs from what vLLM is actually serving right now. */
  protected readonly servingMismatch = computed(() => {
    const served = this.status.value()?.served_model;
    const selected = this.selectedMainId();
    return Boolean(served && selected && served !== selected);
  });

  protected selectMain(id: string): void {
    this.selectedMainId.set(id);
  }

  protected selectVision(id: string): void {
    this.selectedVisionId.set(id);
  }

  protected readonly savingSelection = signal(false);
  protected readonly selectionError = signal<string | null>(null);
  protected readonly selectionSaved = signal(false);

  protected async saveSelection(): Promise<void> {
    const main = this.selectedMain();
    const current = this.config.value();
    if (!main || !current || this.savingSelection()) {
      return;
    }
    this.savingSelection.set(true);
    this.selectionError.set(null);
    this.selectionSaved.set(false);
    const { error } = await this.api.updateAiRuntimeConfig({
      provider: 'vllm',
      base_url: current.base_url,
      model: main.id,
      enabled: true,
    });
    this.savingSelection.set(false);
    if (error) {
      this.selectionError.set(apiErrorMessage(error, 'Failed to save the model selection.'));
      return;
    }
    this.selectionSaved.set(true);
    this.status.reload();
  }

  // --- Advanced (raw) config form -------------------------------------------

  protected readonly form = this.formBuilder.nonNullable.group({
    provider: ['vllm' as AiRuntimeConfig['provider'], Validators.required],
    base_url: ['', Validators.required],
    model: ['', Validators.required],
    enabled: [false],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);
  protected readonly submitSuccess = signal(false);

  constructor() {
    effect(() => {
      const value = this.config.value();
      if (value) {
        this.form.reset({
          provider: value.provider,
          base_url: value.base_url,
          model: value.model,
          enabled: value.enabled ?? false,
        });
        if (this.selectedMainId() === null && value.model) {
          this.selectedMainId.set(value.model);
        }
      }
    });
    effect(() => {
      const status = this.status.value();
      if (status?.vision_model && this.selectedVisionId() === 'none') {
        this.selectedVisionId.set(status.vision_model);
      }
    });
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.submitError.set(null);
    this.submitSuccess.set(false);

    const { error } = await this.api.updateAiRuntimeConfig(this.form.getRawValue());

    this.submitting.set(false);

    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to update AI runtime configuration.'));
      return;
    }

    this.submitSuccess.set(true);
    this.config.reload();
  }
}
