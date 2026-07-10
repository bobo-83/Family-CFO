import { Component, computed, DestroyRef, effect, inject, resource, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import type { AiModelInfo, AiRuntimeConfig, AiSwapStatus } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

const PROVIDERS: AiRuntimeConfig['provider'][] = ['vllm', 'ollama', 'llama_cpp', 'openai_compatible'];

/** Extra memory beyond the weights for KV cache / runtime overhead (ADR 0012). */
const HEADROOM = 1.15;

/** How many browse rows show before "Show more" (M47: no more endless grids). */
const PAGE_SIZE = 6;

const PARAMS_IN_NAME = /(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)/;

export type FitVerdict = 'fits' | 'tight' | 'no' | 'unknown';
export type QuickFilter = 'recommended' | 'vision-big' | 'vision-small' | 'finance' | 'all';

@Component({
  selector: 'app-ai-runtime',
  imports: [FormsModule, ReactiveFormsModule],
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

  // --- Known models (M47) -----------------------------------------------------
  // Curated + HF search results + synthesized stubs for any ACTIVE or SELECTED
  // id that is not otherwise loaded — an applied off-catalog model must never
  // silently disappear from the page.

  /** Estimate a model's shape from its id alone (same heuristics as the API). */
  private stubModel(id: string, roleHint: 'main' | 'vision' = 'main'): AiModelInfo {
    const name = id.split('/').pop() ?? id;
    const match = PARAMS_IN_NAME.exec(name);
    const params = match ? Number(match[1]) : 0;
    const lower = id.toLowerCase();
    const isVision = roleHint === 'vision' || lower.includes('-vl-') || lower.includes('vision');
    return {
      id,
      label: `${name} (not in catalog)`,
      role: isVision && roleHint === 'vision' ? 'vision' : isVision ? 'both' : 'main',
      parameters_b: params,
      est_memory_gb: params ? Math.round(params * 2.1) : 0,
      est_disk_gb: params ? Math.round(params * 2) : 0,
      tool_parser: isVision ? undefined : 'hermes',
      supports_vision: isVision,
      gated: false,
      notes: 'Specs estimated from the model name.',
    };
  }

  private readonly knownModels = computed<AiModelInfo[]>(() => {
    const curated = this.catalog.value() ?? [];
    const ids = new Set(curated.map((m) => m.id));
    const merged = [...curated];
    for (const model of this.searchResults()) {
      if (!ids.has(model.id)) {
        ids.add(model.id);
        merged.push(model);
      }
    }
    const ensure = (id: string | null | undefined, role: 'main' | 'vision') => {
      if (id && id !== 'none' && !ids.has(id)) {
        ids.add(id);
        merged.push(this.stubModel(id, role));
      }
    };
    ensure(this.config.value()?.model, 'main');
    ensure(this.status.value()?.served_model, 'main');
    ensure(this.status.value()?.vision_model, 'vision');
    ensure(this.selectedMainId(), 'main');
    ensure(this.selectedVisionId(), 'vision');
    return merged;
  });

  protected byId(id: string | null | undefined): AiModelInfo | null {
    if (!id || id === 'none') {
      return null;
    }
    return this.knownModels().find((m) => m.id === id) ?? null;
  }

  // --- Model selection (REPLACES the served models; never additive) ---------

  protected readonly selectedMainId = signal<string | null>(null);
  protected readonly selectedVisionId = signal<string>('none');

  protected readonly mainOptions = computed(() =>
    this.knownModels().filter((m) => m.role === 'main' || m.role === 'both'),
  );
  protected readonly visionOptions = computed(() =>
    this.knownModels().filter((m) => m.role === 'vision'),
  );

  protected readonly selectedMain = computed<AiModelInfo | null>(() =>
    this.byId(this.selectedMainId()),
  );

  /** Vision model in play — hidden/ignored when the main model sees photos itself. */
  protected readonly selectedVision = computed<AiModelInfo | null>(() => {
    if (this.selectedVisionId() === 'none' || this.selectedMain()?.supports_vision) {
      return null;
    }
    return this.byId(this.selectedVisionId());
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

  /** Per-model memory fit badge for browse rows (individual, not combined). */
  protected fitOf(model: AiModelInfo): FitVerdict {
    const budget = this.memoryBudgetGb();
    if (!model.est_memory_gb || budget == null) {
      return 'unknown';
    }
    const required = model.est_memory_gb * HEADROOM;
    if (required <= budget * 0.85) {
      return 'fits';
    }
    return required <= budget ? 'tight' : 'no';
  }

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

  /** Role-aware select from the browse list (M47). */
  protected selectFor(model: AiModelInfo): void {
    if (model.role === 'vision') {
      this.selectVision(model.id);
    } else {
      this.selectMain(model.id);
    }
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

  // --- Hugging Face search (ADR 0013) ----------------------------------------

  protected readonly searchQuery = signal('');
  protected readonly searchResults = signal<AiModelInfo[]>([]);
  protected readonly searching = signal(false);
  protected readonly searchError = signal<string | null>(null);

  protected async runSearch(): Promise<void> {
    const query = this.searchQuery().trim();
    if (!query || this.searching()) {
      return;
    }
    this.searching.set(true);
    this.searchError.set(null);
    const { data, error } = await this.api.searchAiModels(query);
    this.searching.set(false);
    if (error || !data) {
      this.searchError.set(
        apiErrorMessage(error, 'Hugging Face is unreachable; showing curated models only.'),
      );
      return;
    }
    this.searchResults.set(data.models);
    // Fresh results: show them, not a stale preset slice.
    this.quickFilter.set('all');
    this.visibleCount.set(PAGE_SIZE);
  }

  // --- Browse filters + sort (M47) --------------------------------------------

  protected readonly quickFilter = signal<QuickFilter>('recommended');
  protected readonly roleFilter = signal<'all' | 'main' | 'vision'>('all');
  protected readonly onlyFits = signal(false);
  protected readonly sortBy = signal<'default' | 'size-desc' | 'size-asc' | 'memory-asc'>(
    'default',
  );
  protected readonly visibleCount = signal(PAGE_SIZE);

  protected setQuickFilter(filter: QuickFilter): void {
    this.quickFilter.set(filter);
    this.visibleCount.set(PAGE_SIZE);
  }

  protected readonly filteredModels = computed<AiModelInfo[]>(() => {
    let models = [...this.knownModels()];
    let order: 'asc' | 'desc' | null = null;

    switch (this.quickFilter()) {
      case 'recommended':
        // Strongest main model that actually fits this server.
        models = models.filter(
          (m) => (m.role === 'main' || m.role === 'both') && this.fitOf(m) !== 'no',
        );
        order = 'desc';
        break;
      case 'vision-big':
        models = models.filter((m) => m.supports_vision);
        order = 'desc';
        break;
      case 'vision-small':
        models = models.filter((m) => m.supports_vision);
        order = 'asc';
        break;
      case 'finance':
        // Financial reasoning needs tool calling; strongest tool-capable first.
        models = models.filter(
          (m) => (m.role === 'main' || m.role === 'both') && Boolean(m.tool_parser),
        );
        order = 'desc';
        break;
      case 'all':
        break;
    }

    const role = this.roleFilter();
    if (role === 'main') {
      models = models.filter((m) => m.role === 'main' || m.role === 'both');
    } else if (role === 'vision') {
      models = models.filter((m) => m.supports_vision);
    }
    if (this.onlyFits()) {
      models = models.filter((m) => this.fitOf(m) === 'fits' || this.fitOf(m) === 'tight');
    }

    const sort = this.sortBy();
    if (sort === 'size-desc') {
      order = 'desc';
    } else if (sort === 'size-asc') {
      order = 'asc';
    }
    if (sort === 'memory-asc') {
      models.sort((a, b) => (a.est_memory_gb || 1e9) - (b.est_memory_gb || 1e9));
    } else if (order === 'desc') {
      models.sort((a, b) => b.parameters_b - a.parameters_b);
    } else if (order === 'asc') {
      // Unknown sizes (0) sort last, not first.
      models.sort((a, b) => (a.parameters_b || 1e9) - (b.parameters_b || 1e9));
    }
    return models;
  });

  protected readonly visibleModels = computed(() =>
    this.filteredModels().slice(0, this.visibleCount()),
  );
  protected readonly hiddenCount = computed(() =>
    Math.max(0, this.filteredModels().length - this.visibleCount()),
  );

  protected showMore(): void {
    this.visibleCount.update((count) => count + PAGE_SIZE);
  }

  // --- One-click apply + live status (ADR 0013) -------------------------------

  protected readonly applyState = signal<AiSwapStatus | null>(null);
  protected readonly applying = signal(false);
  protected readonly applyError = signal<string | null>(null);
  private applyTimer: ReturnType<typeof setInterval> | null = null;

  protected readonly applyLive = computed(() => {
    const state = this.applyState();
    const status = this.status.value();
    if (!state) {
      return null;
    }
    if (state.state === 'failed') {
      return { phase: 'failed' as const, detail: state.log_tail };
    }
    if (
      status?.ready &&
      status.served_model === this.selectedMainId() &&
      state.state !== 'running'
    ) {
      return { phase: 'active' as const, detail: status.served_model ?? '' };
    }
    if (state.state === 'running' || state.state === 'succeeded') {
      // succeeded = containers recreated; the model may still be downloading/loading.
      return { phase: 'working' as const, detail: 'Downloading / loading the model…' };
    }
    return null;
  });

  protected async apply(): Promise<void> {
    const main = this.selectedMain();
    if (!main || this.applying()) {
      return;
    }
    this.applying.set(true);
    this.applyError.set(null);
    const vision = this.selectedVision();
    const { data, error } = await this.api.applyAiModelSelection({
      main_model: main.id,
      vision_model: main.supports_vision ? undefined : (vision?.id ?? undefined),
    });
    this.applying.set(false);
    if (error || !data) {
      this.applyError.set(
        apiErrorMessage(error, 'Could not start the model swap. Is the model manager running?'),
      );
      return;
    }
    this.applyState.set(data);
    this.startPolling();
  }

  /** Poll swap + serving status every 5s until the selection is active or fails. */
  protected startPolling(): void {
    this.stopPolling();
    this.applyTimer = setInterval(() => void this.pollOnce(), 5000);
  }

  protected stopPolling(): void {
    if (this.applyTimer !== null) {
      clearInterval(this.applyTimer);
      this.applyTimer = null;
    }
  }

  protected async pollOnce(): Promise<void> {
    const { data } = await this.api.getAiApplyStatus();
    if (data) {
      this.applyState.set(data);
    }
    this.status.reload();
    const live = this.applyLive();
    if (live?.phase === 'active' || live?.phase === 'failed') {
      this.stopPolling();
    }
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

  // M47: the vision model is deployment-level (swap path, ADR 0013), so the
  // Advanced section changes it through the apply endpoint — not the raw PUT.
  protected readonly visionModelInput = signal('');
  protected readonly applyingVision = signal(false);
  protected readonly visionApplyError = signal<string | null>(null);

  protected async applyVisionModel(): Promise<void> {
    const main = this.config.value()?.model;
    if (!main || this.applyingVision()) {
      return;
    }
    this.applyingVision.set(true);
    this.visionApplyError.set(null);
    const vision = this.visionModelInput().trim();
    const { data, error } = await this.api.applyAiModelSelection({
      main_model: main,
      vision_model: vision || undefined,
    });
    this.applyingVision.set(false);
    if (error || !data) {
      this.visionApplyError.set(
        apiErrorMessage(error, 'Could not start the vision swap. Is the model manager running?'),
      );
      return;
    }
    this.applyState.set(data);
    this.startPolling();
  }

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopPolling());
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
      if (status?.vision_model && !this.visionModelInput()) {
        this.visionModelInput.set(status.vision_model);
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
