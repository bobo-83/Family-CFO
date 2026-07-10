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

/** How many live results each quick filter pulls from Hugging Face (M48). */
const LIVE_LIMIT = 20;

const PARAMS_IN_NAME = /(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)/;

export type FitVerdict = 'fits' | 'tight' | 'no' | 'unknown';
export type QuickFilter = 'recommended' | 'vision-big' | 'vision-small' | 'finance' | 'all';

/** What would actually be served if this model were applied (M48). */
export interface ApplyPlan {
  mainId: string;
  visionId: string | null;
  description: string;
  memoryGb: number | null;
  diskGb: number | null;
  memoryVerdict: FitVerdict;
  diskVerdict: FitVerdict;
}

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

  // --- Known models: curated + live results + stubs for active ids (M47/M48) --

  /** Estimate a model's shape from its id alone (same heuristics as the API). */
  private stubModel(id: string, roleHint: 'main' | 'vision' = 'main'): AiModelInfo {
    const name = id.split('/').pop() ?? id;
    const match = PARAMS_IN_NAME.exec(name);
    const params = match ? Number(match[1]) : 0;
    const lower = id.toLowerCase();
    const isVision = roleHint === 'vision' || lower.includes('-vl-') || lower.includes('vision');
    // M49: quantization markers change the bytes/param dramatically.
    const gbPerB = /awq|gptq|int4|4bit|4-bit/.test(lower)
      ? 0.65
      : /fp8|int8|8bit|8-bit/.test(lower)
        ? 1.1
        : 2.1;
    return {
      id,
      label: `${name} (not in catalog)`,
      role: isVision && roleHint === 'vision' ? 'vision' : isVision ? 'both' : 'main',
      parameters_b: params,
      est_memory_gb: params ? Math.round(params * gbPerB) : 0,
      est_disk_gb: params ? Math.round(params * gbPerB * 0.95) : 0,
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
    return merged;
  });

  protected byId(id: string | null | undefined): AiModelInfo | null {
    if (!id || id === 'none') {
      return null;
    }
    return this.knownModels().find((m) => m.id === id) ?? null;
  }

  /** The chat model currently in play (served, else configured). */
  private currentMainId(): string | null {
    return this.status.value()?.served_model ?? this.config.value()?.model ?? null;
  }

  /** Memory budget: GPU memory when known, else system RAM (unified/unknown GPU). */
  protected readonly memoryBudgetGb = computed(() => {
    const hw = this.hardware.value();
    return hw?.gpu_memory_gb ?? hw?.system_memory_gb ?? null;
  });

  protected readonly usingSystemMemoryBudget = computed(() => {
    const hw = this.hardware.value();
    return hw != null && hw.gpu_memory_gb == null && hw.system_memory_gb != null;
  });

  private memoryVerdictFor(requiredGb: number | null): FitVerdict {
    const budget = this.memoryBudgetGb();
    if (requiredGb == null || budget == null) {
      return 'unknown';
    }
    if (requiredGb <= budget * 0.85) {
      return 'fits';
    }
    return requiredGb <= budget ? 'tight' : 'no';
  }

  private diskVerdictFor(requiredGb: number | null): FitVerdict {
    const free = this.hardware.value()?.disk_free_gb ?? null;
    if (requiredGb == null || free == null) {
      return 'unknown';
    }
    // 1.5x covers download + extraction; already-cached weights aren't counted.
    if (requiredGb * 1.5 <= free) {
      return 'fits';
    }
    return requiredGb <= free ? 'tight' : 'no';
  }

  /** Per-model memory fit badge for collapsed browse rows (individual). */
  protected fitOf(model: AiModelInfo): FitVerdict {
    if (!model.est_memory_gb) {
      return 'unknown';
    }
    return this.memoryVerdictFor(model.est_memory_gb * HEADROOM);
  }

  /**
   * M48: the serving plan if this model were applied — a photo-blind main keeps
   * the current photo model; a vision-capable main replaces both; a vision
   * model pairs with the current chat model. Fit is for the combination.
   */
  protected planFor(model: AiModelInfo): ApplyPlan {
    let mainId: string;
    let visionId: string | null;
    let description: string;
    if (model.role === 'vision') {
      mainId = this.currentMainId() ?? model.id;
      visionId = model.id;
      description = `Photos: this model · Chat: ${mainId}`;
    } else if (model.supports_vision) {
      mainId = model.id;
      visionId = null;
      description = 'Chat + photos: this model (sees photos itself)';
    } else {
      mainId = model.id;
      visionId = this.status.value()?.vision_model ?? null;
      description = visionId
        ? `Chat: this model · Photos: ${visionId} (kept)`
        : 'Chat: this model · Photos: off';
    }

    const main = mainId === model.id ? model : this.byId(mainId);
    const vision = visionId === null ? null : visionId === model.id ? model : this.byId(visionId);
    const mainMem = main?.est_memory_gb || 0;
    const visionMem = vision?.est_memory_gb || 0;
    const known = mainMem > 0 && (visionId === null || visionMem > 0);
    const memoryGb = known ? Math.round((mainMem + visionMem) * HEADROOM) : null;
    const diskGb = known
      ? Math.round((main?.est_disk_gb || 0) + (vision?.est_disk_gb || 0))
      : null;
    return {
      mainId,
      visionId,
      description,
      memoryGb,
      diskGb,
      memoryVerdict: this.memoryVerdictFor(memoryGb),
      diskVerdict: this.diskVerdictFor(diskGb),
    };
  }

  // --- Row expansion + apply (M48) --------------------------------------------

  protected readonly expandedId = signal<string | null>(null);

  protected toggleExpand(id: string): void {
    this.expandedId.update((current) => (current === id ? null : id));
  }

  protected readonly applyState = signal<AiSwapStatus | null>(null);
  protected readonly applying = signal(false);
  protected readonly applyError = signal<string | null>(null);
  /** The main model the last apply targeted — drives the "active" phase. */
  protected readonly lastAppliedMain = signal<string | null>(null);
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
      status.served_model === this.lastAppliedMain() &&
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

  protected async applyModel(model: AiModelInfo): Promise<void> {
    if (this.applying()) {
      return;
    }
    const plan = this.planFor(model);
    this.applying.set(true);
    this.applyError.set(null);
    const { data, error } = await this.api.applyAiModelSelection({
      main_model: plan.mainId,
      vision_model: plan.visionId ?? undefined,
    });
    this.applying.set(false);
    if (error || !data) {
      this.applyError.set(
        apiErrorMessage(error, 'Could not start the model swap. Is the model manager running?'),
      );
      return;
    }
    this.lastAppliedMain.set(plan.mainId);
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

  // --- Live quick filters + search (M48) ---------------------------------------

  protected readonly searchQuery = signal('');
  protected readonly searchResults = signal<AiModelInfo[]>([]);
  protected readonly searching = signal(false);
  protected readonly searchError = signal<string | null>(null);

  protected readonly quickFilter = signal<QuickFilter>('recommended');
  protected readonly roleFilter = signal<'all' | 'main' | 'vision'>('all');
  protected readonly onlyFits = signal(false);
  protected readonly sortBy = signal<'default' | 'size-desc' | 'size-asc' | 'memory-asc'>(
    'default',
  );
  protected readonly visibleCount = signal(PAGE_SIZE);

  private async fetchLive(
    ...requests: { q?: string; pipeline?: 'any' | 'text-generation' | 'image-text-to-text' }[]
  ): Promise<void> {
    this.searching.set(true);
    this.searchError.set(null);
    const responses = await Promise.all(
      requests.map((options) => this.api.searchAiModels({ ...options, limit: LIVE_LIMIT })),
    );
    this.searching.set(false);
    const merged: AiModelInfo[] = [];
    const seen = new Set<string>();
    let failures = 0;
    for (const { data, error } of responses) {
      if (error || !data) {
        failures += 1;
        continue;
      }
      for (const model of data.models) {
        if (!seen.has(model.id)) {
          seen.add(model.id);
          merged.push(model);
        }
      }
    }
    if (failures === responses.length) {
      this.searchError.set('Hugging Face is unreachable; showing curated models only.');
      return;
    }
    this.searchResults.set(merged);
  }

  protected async runSearch(): Promise<void> {
    const query = this.searchQuery().trim();
    if (!query || this.searching()) {
      return;
    }
    this.quickFilter.set('all');
    this.visibleCount.set(PAGE_SIZE);
    await this.fetchLive({ q: query, pipeline: 'any' });
  }

  /** M48: quick filters fetch a live list from the HF catalog, then shape it. */
  protected async setQuickFilter(filter: QuickFilter): Promise<void> {
    this.quickFilter.set(filter);
    this.visibleCount.set(PAGE_SIZE);
    switch (filter) {
      case 'recommended':
        await this.fetchLive({ pipeline: 'text-generation' });
        break;
      case 'finance':
        await this.fetchLive({ q: 'finance', pipeline: 'text-generation' });
        break;
      case 'vision-big':
      case 'vision-small':
        // Fan out beyond the download charts: genuinely large vision models
        // are rarely download leaders (M49).
        await this.fetchLive(
          { pipeline: 'image-text-to-text' },
          { q: '72B', pipeline: 'image-text-to-text' },
          { q: 'vision', pipeline: 'image-text-to-text' },
        );
        break;
      case 'all':
        break;
    }
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
        models = models.filter((m) => m.supports_vision && this.fitOf(m) !== 'no');
        order = 'desc';
        break;
      case 'vision-small':
        models = models.filter((m) => m.supports_vision && this.fitOf(m) !== 'no');
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
    this.lastAppliedMain.set(main);
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
      }
    });
    effect(() => {
      const status = this.status.value();
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
