import { NgTemplateOutlet } from '@angular/common';
import {
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  resource,
  signal,
  viewChild,
} from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type { AiModelDetail, AiModelInfo, AiRuntimeConfig, AiSwapStatus } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

const PROVIDERS: AiRuntimeConfig['provider'][] = [
  'vllm',
  'ollama',
  'llama_cpp',
  'openai_compatible',
];

/** Extra memory beyond the weights for KV cache / runtime overhead (ADR 0012). */
const HEADROOM = 1.15;

/** How many browse rows show before "Show more" (M47: no more endless grids). */
const PAGE_SIZE = 6;

/** How many live results each quick filter pulls from Hugging Face (M48). */
const LIVE_LIMIT = 20;

const PARAMS_IN_NAME = /(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)/;

/** Known-obsolete families: size ranking is generation-blind, so a 2024-era
 *  110B would outrank a modern 72B. Excluded from the ranked recommendations
 *  only — the "All" filter still shows everything (M54). */
const LEGACY_FAMILIES = /qwen1[._-]?5|llama-?2\b|falcon|vicuna|mpt-|gpt2|gpt-j|bloom/i;

export type FitVerdict = 'fits' | 'tight' | 'no' | 'unknown';
export type QuickFilter =
  'recommended' | 'all-in-one' | 'vision-big' | 'vision-small' | 'finance' | 'all';

/** Which accepted live response a fetch commits to (#168). */
type LiveFetchTarget = 'browse' | 'search';

/** Describes the current live operation (browse or search) (#168). */
type LiveLoadState = 'idle' | 'loading' | 'success' | 'error';

/** A settled live fetch: models on success, a formatted message on failure. */
type LiveFetchOutcome = { ok: true; models: AiModelInfo[] } | { ok: false; message: string };

/** searchAiModels options minus limit — fetchLive always supplies LIVE_LIMIT. */
type LiveSearchOptions = {
  q?: string;
  pipeline?: 'any' | 'text-generation' | 'image-text-to-text';
  deep?: boolean;
};

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
  imports: [
    NgTemplateOutlet,
    FormsModule,
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatCheckboxModule,
    MatButtonModule,
  ],
  templateUrl: './ai-runtime.html',
  styleUrl: './ai-runtime.scss',
})
export class AiRuntime {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  // ADR 0065: model swaps are box-global — only a SYSTEM ADMIN may apply.
  // The session's rights include ai_runtime.manage exactly when the user is
  // on the box's system-admin roster (re-login picks up a new grant).
  protected readonly canManageRuntime = () => this.auth.hasRight('ai_runtime.manage');
  private readonly formBuilder = inject(FormBuilder);

  protected readonly providers = PROVIDERS;
  protected readonly isOwner = () => this.auth.hasRight('ai_runtime.manage');

  /** Shown where no photo model is being served. */
  protected readonly offLabel = $localize`:Runtime model value|No photo model is being served:off`;

  /**
   * Tooltip on the Apply buttons — empty for an admin, otherwise the reason the
   * button is disabled. Built here because an interpolated i18n-title would be
   * applied as a JS expando and silently vanish from the DOM.
   */
  protected applyDisabledTitle(): string {
    return this.canManageRuntime()
      ? ''
      : $localize`:Apply button tooltip|Explains why only a system administrator may switch the box-wide model:Only a system administrator can switch the box-wide model (ADR 0065)`;
  }

  protected readonly config = resource({
    loader: async () => {
      const { data, error } = await this.api.getAiRuntimeConfig();
      if (error) {
        throw new Error(
          apiErrorMessage(
            error,
            $localize`:Error message|The AI runtime configuration could not be loaded:Failed to load AI runtime configuration.`,
          ),
        );
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
        throw new Error(
          apiErrorMessage(
            error,
            $localize`:Error message|The list of available models could not be loaded:Failed to load the model catalog.`,
          ),
        );
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

  // #181: per-household share of the box — operator telemetry, fails quiet
  // (null on 403 for non-admins or any transport error; the section just hides).
  protected readonly usage = resource({
    loader: async () => {
      try {
        const { data } = await this.api.getAiUsage();
        return data ?? null;
      } catch {
        return null;
      }
    },
  });

  /** A single-family box with the cap off needs no fairness view (#181). */
  protected readonly usageVisible = computed(() => {
    const usage = this.usage.value();
    if (!usage) {
      return false;
    }
    return usage.households.length >= 2 || usage.chat_hourly_limit > 0;
  });

  /** #196: any household with members but no member key yet — surfaces the footnote. */
  protected readonly memberSignInNeeded = computed(() =>
    (this.usage.value()?.households ?? []).some((row) => row.member_key_ok === false),
  );

  /** "512 MB" / "1.4 GB" — decimal units. */
  protected formatStorage(bytes: number): string {
    if (bytes >= 1e9) {
      return `${(bytes / 1e9).toFixed(1)} GB`;
    }
    return `${Math.round(bytes / 1e6)} MB`;
  }

  // ADR 0040: how much of the transaction history the advisor has studied.
  protected readonly study = resource({
    loader: async () => {
      const { data } = await this.api.getAiStudyStatus();
      return data ?? null;
    },
  });

  protected readonly studyInsightsShown = signal(5);

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
      label: $localize`:Model label|A model that is not in the curated catalog, so its specs are guessed:${name}:name: (not in catalog)`,
      role: isVision && roleHint === 'vision' ? 'vision' : isVision ? 'both' : 'main',
      parameters_b: params,
      est_memory_gb: params ? Math.round(params * gbPerB) : 0,
      est_disk_gb: params ? Math.round(params * gbPerB * 0.95) : 0,
      tool_parser: isVision ? undefined : 'hermes',
      supports_vision: isVision,
      gated: false,
      notes: $localize`:Model note|The model's specs were guessed from its identifier:Specs estimated from the model name.`,
    };
  }

  /** Stable first-wins merge; dedupe by case-insensitive repo id (#168). */
  private mergeModels(...sources: AiModelInfo[][]): AiModelInfo[] {
    const seen = new Set<string>();
    const merged: AiModelInfo[] = [];
    for (const source of sources) {
      for (const model of source) {
        const key = model.id.toLowerCase();
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(model);
        }
      }
    }
    return merged;
  }

  /** Stubs for the configured/served ids, in configured-main → served order. */
  /** Stubs for the configured/served ids, in configured-main → served order.
   *  Deduped case-insensitively (#168): when configured and served (or vision)
   *  ids refer to the same off-catalog model, it contributes one stub — two
   *  would render duplicate rows with duplicate @for track keys (NG0955). */
  private activeStubs(): AiModelInfo[] {
    const stubs = new Map<string, AiModelInfo>();
    const ensure = (id: string | null | undefined, role: 'main' | 'vision') => {
      if (!id || id === 'none') {
        return;
      }
      const key = id.toLowerCase();
      if (!stubs.has(key)) {
        stubs.set(key, this.stubModel(id, role));
      }
    };
    ensure(this.safeValue(this.config)?.model, 'main');
    ensure(this.safeValue(this.status)?.served_model, 'main');
    ensure(this.safeValue(this.status)?.vision_model, 'vision');
    return [...stubs.values()];
  }

  /** Lookup universe: curated + query results + browse results + stubs. */
  private readonly knownModels = computed<AiModelInfo[]>(() =>
    this.mergeModels(
      this.safeValue(this.catalog) ?? [],
      this.searchResults(),
      this.browseLiveResults(),
      this.activeStubs(),
    ),
  );

  /** Browse display universe — query responses never leak back in (#168). */
  private readonly browseModels = computed<AiModelInfo[]>(() =>
    this.mergeModels(
      this.safeValue(this.catalog) ?? [],
      this.browseLiveResults(),
      this.activeStubs(),
    ),
  );

  protected byId(id: string | null | undefined): AiModelInfo | null {
    if (!id || id === 'none') {
      return null;
    }
    const key = id.toLowerCase();
    return this.knownModels().find((m) => m.id.toLowerCase() === key) ?? null;
  }

  /** The chat model currently in play (served, else configured). */
  private currentMainId(): string | null {
    return this.status.value()?.served_model ?? this.config.value()?.model ?? null;
  }

  /** Median felt latency per model — present even while the runtime is off. */
  protected readonly answerStats = computed(() => this.status.value()?.answer_stats ?? []);

  /** "8.3s" under a minute, "1m 24s" above — felt latency, not benchmarks. */
  protected formatAnswerTime(ms: number): string {
    const seconds = ms / 1000;
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s`;
    }
    const total = Math.round(seconds);
    const rest = total % 60;
    return rest ? `${Math.floor(total / 60)}m ${rest}s` : `${Math.floor(total / 60)}m`;
  }

  /** M50: short label for the not-ready state ("downloading" beats "loading"). */
  protected statusPhaseLabel(): string {
    switch (this.status.value()?.loading_phase) {
      case 'downloading':
        return $localize`:Runtime phase|The weights are being downloaded:downloading`;
      case 'loading':
        return $localize`:Runtime phase|The downloaded weights are being loaded into memory:loading weights`;
      case 'warming_up':
        return $localize`:Runtime phase|The model is loaded and warming up:warming up`;
      case 'starting':
        return $localize`:Runtime phase|The serving container is starting:starting`;
      default:
        return $localize`:Runtime phase|The model is either loading or not running at all:loading / off`;
    }
  }

  /** Memory budget: GPU memory when known, else system RAM (unified/unknown GPU). */
  protected readonly memoryBudgetGb = computed(() => {
    const hw = this.hardware.value();
    const node = hw?.gpu_memory_gb ?? hw?.system_memory_gb ?? null;
    if (node == null) {
      return null;
    }
    // #182: the resident vision describer's slot isn't available to the main
    // model — judge fits against what's actually left.
    return node - (hw?.vision_reserved_gb ?? 0);
  });

  protected readonly usingSystemMemoryBudget = computed(() => {
    const hw = this.hardware.value();
    return hw != null && hw.gpu_memory_gb == null && hw.system_memory_gb != null;
  });

  // --- Two-box cluster (ADR 0071) --------------------------------------------

  protected readonly clusterPeerHost = computed(
    () => this.hardware.value()?.cluster_peer_host ?? null,
  );
  protected readonly clusterPeerReachable = computed(
    () => this.hardware.value()?.cluster_peer_reachable ?? false,
  );
  /** Combined budget across both boxes; null while the peer is unreachable. */
  protected readonly clusterMemoryGb = computed(
    () => this.hardware.value()?.cluster_memory_gb ?? null,
  );
  protected readonly clusterEnabled = computed(() => this.config.value()?.cluster_enabled ?? false);
  /** Cluster models are offered only with the toggle ON and the peer reachable. */
  protected readonly clusterActive = computed(
    () => this.clusterEnabled() && this.clusterPeerReachable(),
  );

  protected isClusterModel(model: AiModelInfo): boolean {
    return (model.min_nodes ?? 1) >= 2;
  }

  /**
   * Needs the second box: either the catalog says so (min_nodes >= 2), or the
   * model's estimated memory (with headroom) exceeds this box's own budget —
   * the same budget the single-box fit verdict uses.
   */
  protected needsCluster(model: AiModelInfo): boolean {
    if (this.isClusterModel(model)) {
      return true;
    }
    const budget = this.memoryBudgetGb();
    return budget != null && !!model.est_memory_gb && model.est_memory_gb * HEADROOM > budget;
  }

  /** True when the model is offered (and fit-judged) as a cluster deployment. */
  protected servesOnCluster(model: AiModelInfo): boolean {
    return this.clusterActive() && this.needsCluster(model);
  }

  protected readonly savingCluster = signal(false);
  protected readonly clusterError = signal<string | null>(null);

  /** Flip cluster_enabled through the existing runtime-config PUT (full object). */
  protected async setClusterEnabled(on: boolean): Promise<void> {
    const current = this.config.value();
    if (!current || this.savingCluster()) {
      return;
    }
    this.savingCluster.set(true);
    this.clusterError.set(null);
    const { error } = await this.api.updateAiRuntimeConfig({
      provider: current.provider,
      base_url: current.base_url,
      model: current.model,
      enabled: current.enabled ?? false,
      cluster_enabled: on,
    });
    this.savingCluster.set(false);
    if (error) {
      this.clusterError.set(
        apiErrorMessage(
          error,
          $localize`:Error message|The two-box cluster toggle could not be saved:Could not update the cluster setting.`,
        ),
      );
      return;
    }
    this.config.reload();
  }

  private verdictAgainst(requiredGb: number | null, budget: number | null): FitVerdict {
    if (requiredGb == null || budget == null) {
      return 'unknown';
    }
    if (requiredGb <= budget * 0.85) {
      return 'fits';
    }
    return requiredGb <= budget ? 'tight' : 'no';
  }

  private memoryVerdictFor(requiredGb: number | null, cluster = false): FitVerdict {
    // ADR 0071: cluster-tier entries are judged against the COMBINED budget.
    return this.verdictAgainst(
      requiredGb,
      cluster ? this.clusterMemoryGb() : this.memoryBudgetGb(),
    );
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
  // M71: a model counts as modern when released within ~18 months; curated
  // entries carry no created_at and are treated as modern (hand-vetted).
  protected isModern(model: AiModelInfo): boolean {
    if (!model.created_at) {
      return true;
    }
    const ageMs = Date.now() - new Date(model.created_at).getTime();
    return Number.isNaN(ageMs) || ageMs < 548 * 24 * 3600 * 1000;
  }

  protected fitOf(model: AiModelInfo): FitVerdict {
    if (!model.est_memory_gb) {
      return 'unknown';
    }
    return this.memoryVerdictFor(model.est_memory_gb * HEADROOM, this.servesOnCluster(model));
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
      description = $localize`:Apply plan|This model would analyse photos while another keeps answering chat:Photos: this model · Chat: ${mainId}:mainModel:`;
    } else if (model.supports_vision) {
      mainId = model.id;
      visionId = null;
      description = $localize`:Apply plan|One model would answer chat and analyse photos:Chat + photos: this model (sees photos itself)`;
    } else {
      mainId = model.id;
      visionId = this.status.value()?.vision_model ?? null;
      description = visionId
        ? $localize`:Apply plan|This model would answer chat while the current photo model is kept:Chat: this model · Photos: ${visionId}:visionModel: (kept)`
        : $localize`:Apply plan|This model would answer chat and no photo model would run:Chat: this model · Photos: off`;
    }

    const main = mainId === model.id ? model : this.byId(mainId);
    const vision = visionId === null ? null : visionId === model.id ? model : this.byId(visionId);
    const mainMem = main?.est_memory_gb || 0;
    const visionMem = vision?.est_memory_gb || 0;
    const known = mainMem > 0 && (visionId === null || visionMem > 0);
    const memoryGb = known ? Math.round((mainMem + visionMem) * HEADROOM) : null;
    const diskGb = known ? Math.round((main?.est_disk_gb || 0) + (vision?.est_disk_gb || 0)) : null;
    return {
      mainId,
      visionId,
      description,
      memoryGb,
      diskGb,
      memoryVerdict: this.memoryVerdictFor(memoryGb, this.servesOnCluster(model)),
      diskVerdict: this.diskVerdictFor(diskGb),
    };
  }

  // --- Row expansion + apply (M48) --------------------------------------------

  protected readonly expandedId = signal<string | null>(null);

  protected toggleExpand(id: string): void {
    this.expandedId.update((current) => (current === id ? null : id));
    if (this.expandedId() === id) {
      void this.loadHubDetail(id);
    }
  }

  // Drill-down (user request 2026-07-22): the expanded row fetches the hub's
  // live stats — downloads, likes, license, last update — via the box (the
  // browser never talks to Hugging Face directly).
  protected readonly hubDetail = signal<AiModelDetail | null>(null);
  protected readonly hubDetailLoading = signal(false);

  private async loadHubDetail(id: string): Promise<void> {
    this.hubDetail.set(null);
    this.hubDetailLoading.set(true);
    try {
      const { data } = await this.api.getAiModelDetail(id);
      // Ignore a late response for a row that is no longer expanded.
      if (this.expandedId() === id) {
        this.hubDetail.set(data ?? null);
      }
    } catch {
      // Hub stats are enrichment — their absence must never break the row.
    } finally {
      if (this.expandedId() === id) {
        this.hubDetailLoading.set(false);
      }
    }
  }

  protected formatHubDate(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
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
      return {
        phase: 'working' as const,
        detail: $localize`:Model swap detail|The swap is running: the weights are being downloaded or loaded:Downloading / loading the model…`,
      };
    }
    return null;
  });

  protected async applyModel(model: AiModelInfo): Promise<void> {
    if (this.applying()) {
      return;
    }
    const plan = this.planFor(model);
    // ADR 0071: applies from the cluster section serve across both boxes; the
    // server answers 409 (surfaced via applyError) if the peer is down.
    await this.postApply(plan.mainId, plan.visionId ?? undefined, this.servesOnCluster(model));
  }

  /** M51: pair a vision-capable model with the CURRENT chat model (photos only). */
  protected async applyAsVision(model: AiModelInfo): Promise<void> {
    if (this.applying()) {
      return;
    }
    const main = this.currentMainId();
    if (!main) {
      this.applyError.set(
        $localize`:Error message|A photo model cannot be applied because no chat model is configured:No chat model is configured to pair with.`,
      );
      return;
    }
    if (main === model.id) {
      // Same model both roles = ONE instance handling both, never two.
      await this.applyModel(model);
      return;
    }
    await this.postApply(main, model.id);
  }

  private async postApply(
    mainId: string,
    visionId: string | undefined,
    cluster = false,
  ): Promise<void> {
    this.applying.set(true);
    this.applyError.set(null);
    const { data, error } = await this.api.applyAiModelSelection({
      main_model: mainId,
      vision_model: visionId,
      // Single-box applies omit the flag (same as cluster: false).
      ...(cluster ? { cluster: true } : {}),
    });
    this.applying.set(false);
    if (error || !data) {
      this.applyError.set(
        apiErrorMessage(
          error,
          $localize`:Error message|The model swap request failed to start:Could not start the model swap. Is the model manager running?`,
        ),
      );
      return;
    }
    this.lastAppliedMain.set(mainId);
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

  /** Editable draft — the submitted query, not this, owns the results (#168). */
  protected readonly searchQuery = signal('');
  /** The trimmed submitted query; null = browse mode. */
  protected readonly submittedQuery = signal<string | null>(null);
  /** The current submitted query's accepted response (search rows only). */
  protected readonly searchResults = signal<AiModelInfo[]>([]);
  /** The last accepted quick-filter response (browse rows only). */
  protected readonly browseLiveResults = signal<AiModelInfo[]>([]);
  /** Describes the current live operation (browse or search). */
  protected readonly liveLoadState = signal<LiveLoadState>('idle');
  protected readonly searching = computed(() => this.liveLoadState() === 'loading');
  protected readonly searchError = signal<string | null>(null);

  protected readonly searchActive = computed(() => this.submittedQuery() !== null);

  /** Monotonically increasing; every new intent invalidates outstanding work. */
  private liveGeneration = 0;
  private destroyed = false;

  private readonly searchInput = viewChild<ElementRef<HTMLInputElement>>('searchInput');

  /** Supersede is allowed while loading; only a blank draft or the identical
   *  in-flight query blocks submission (trim-only identity). */
  protected readonly searchSubmitDisabled = computed(
    () =>
      !this.searchQuery().trim() ||
      (this.liveLoadState() === 'loading' && this.submittedQuery() === this.searchQuery().trim()),
  );

  /** A completion may commit only while it is the newest intent and alive. */
  private canCommit(generation: number): boolean {
    return !this.destroyed && generation === this.liveGeneration;
  }

  /** Errored resources throw from value() — degrade to undefined instead. */
  private safeValue<T>(ref: { hasValue(): boolean; value(): T | undefined }): T | undefined {
    return ref.hasValue() ? ref.value() : undefined;
  }

  protected readonly quickFilter = signal<QuickFilter>('recommended');
  protected readonly roleFilter = signal<'all' | 'main' | 'vision'>('all');
  protected readonly onlyFits = signal(false);
  protected readonly sortBy = signal<'default' | 'size-desc' | 'size-asc' | 'memory-asc'>(
    'default',
  );
  protected readonly visibleCount = signal(PAGE_SIZE);

  /**
   * One live fetch with an explicit destination (#168). Every completion write
   * — commit, failure, or state change — requires the captured generation to
   * still be current and the component alive; there is no unconditional
   * finally. Superseded requests finish in transport and are discarded.
   */
  private async fetchLive(
    target: LiveFetchTarget,
    options: LiveSearchOptions,
  ): Promise<LiveFetchOutcome> {
    const generation = ++this.liveGeneration;
    this.liveLoadState.set('loading');
    this.searchError.set(null);
    let outcome: LiveFetchOutcome;
    try {
      const { data, error, response } = await this.api.searchAiModels({
        ...options,
        limit: LIVE_LIMIT,
      });
      if (error || !data || !Array.isArray(data.models)) {
        outcome = { ok: false, message: this.liveFailureMessage(error, response, target) };
      } else {
        outcome = { ok: true, models: data.models };
      }
    } catch (error) {
      outcome = { ok: false, message: this.liveFailureMessage(error, undefined, target) };
    }
    if (!this.canCommit(generation)) {
      // Superseded (or destroyed): commit nothing, touch no signal.
      return { ok: false, message: '' };
    }
    if (outcome.ok) {
      if (target === 'search') {
        this.searchResults.set(outcome.models);
      } else {
        this.browseLiveResults.set(outcome.models);
      }
      this.liveLoadState.set('success');
      this.searchError.set(null);
    } else {
      // Failure clears only this destination — an older query's rows or a
      // stale browse list must never survive under the new intent.
      if (target === 'search') {
        this.searchResults.set([]);
      } else {
        this.browseLiveResults.set([]);
      }
      this.searchError.set(outcome.message);
      this.liveLoadState.set('error');
    }
    return outcome;
  }

  /**
   * Search failures carry dashboard-owned copy: the endpoint's only non-auth
   * failure is a 503 whose server detail ("showing curated models only") is
   * browse-phrased and false in search mode. Browse keeps the structured
   * pass-through — that detail is accurate there.
   */
  private liveFailureMessage(
    error: unknown,
    response: Response | undefined,
    target: LiveFetchTarget,
  ): string {
    if (target === 'search') {
      const fallback = $localize`:Error message|The live model search failed; Hugging Face is a product name:Could not search Hugging Face. Search again to retry.`;
      return response?.status === 503 ? fallback : apiErrorMessage(error, fallback, response);
    }
    return apiErrorMessage(
      error,
      $localize`:Error message|The live model hub could not be reached; Hugging Face is a product name:Hugging Face is unreachable; showing curated models only.`,
      response,
    );
  }

  protected async runSearch(): Promise<void> {
    const query = this.searchQuery().trim();
    if (!query) {
      this.clearSearch();
      return;
    }
    // Identical in-flight query is a no-op; any other query supersedes it.
    if (this.liveLoadState() === 'loading' && this.submittedQuery() === query) {
      return;
    }
    this.submittedQuery.set(query);
    this.searchResults.set([]);
    this.resetModelWindow();
    await this.fetchLive('search', { q: query, pipeline: 'any' });
  }

  /** Draft edits never touch the submitted query's results; empty exits search. */
  protected onSearchInput(value: string): void {
    this.searchQuery.set(value);
    if (!value.trim() && this.searchActive()) {
      this.clearSearch();
    }
  }

  /** Exit search and restore browsing — without refetching. */
  protected clearSearch(): void {
    this.liveGeneration += 1;
    this.searchQuery.set('');
    this.submittedQuery.set(null);
    this.searchResults.set([]);
    this.liveLoadState.set('idle');
    this.searchError.set(null);
    this.resetModelWindow();
    this.searchInput()?.nativeElement.focus();
  }

  /** Reset pagination and row expansion together (used by every view change). */
  private resetModelWindow(): void {
    this.visibleCount.set(PAGE_SIZE);
    this.expandedId.set(null);
  }

  protected setRoleFilter(value: 'all' | 'main' | 'vision'): void {
    this.roleFilter.set(value);
    this.resetModelWindow();
  }

  protected setSortBy(value: 'default' | 'size-desc' | 'size-asc' | 'memory-asc'): void {
    this.sortBy.set(value);
    this.resetModelWindow();
  }

  protected setOnlyFits(value: boolean): void {
    this.onlyFits.set(value);
    this.resetModelWindow();
  }

  /** M48: quick filters fetch a live list from the HF catalog, then shape it. */
  protected async setQuickFilter(filter: QuickFilter): Promise<void> {
    // Quick filters are browse navigation: leaving search discards the query
    // and invalidates any pending fetch. browseLiveResults is retained — it is
    // replaced by (or cleared on failure of) the new fetch, matching today's
    // browse behavior (D3 changes only the failure branch).
    this.liveGeneration += 1;
    if (this.searchActive()) {
      this.searchQuery.set('');
      this.submittedQuery.set(null);
      this.searchResults.set([]);
    }
    this.searchError.set(null);
    this.liveLoadState.set('idle');
    this.quickFilter.set(filter);
    this.resetModelWindow();
    switch (filter) {
      case 'recommended':
        // M53: deep fan-out — the largest FITTING models, not just the popular.
        await this.fetchLive('browse', { pipeline: 'text-generation', deep: true });
        break;
      case 'all-in-one':
        await this.fetchLive('browse', { pipeline: 'any', deep: true });
        break;
      case 'finance':
        await this.fetchLive('browse', { q: 'finance', pipeline: 'text-generation', deep: true });
        break;
      case 'vision-big':
      case 'vision-small':
        await this.fetchLive('browse', { pipeline: 'image-text-to-text', deep: true });
        break;
      case 'all':
        break;
    }
  }

  /** ADR 0071: models that need both boxes, shown only while clustering is on. */
  protected readonly clusterModels = computed<AiModelInfo[]>(() =>
    this.clusterActive() ? this.knownModels().filter((m) => this.needsCluster(m)) : [],
  );

  protected readonly filteredModels = computed<AiModelInfo[]>(() =>
    this.filterAndSortModels(
      this.browseModels().filter((m) =>
        this.clusterActive() ? !this.needsCluster(m) : !this.isClusterModel(m),
      ),
      this.quickFilter(),
    ),
  );

  /**
   * Shared role/fit/sort shaping for browse and search (#168). Always works on
   * a copy: the quick filter supplies browse's recommendation predicates while
   * search passes the neutral 'all' with the user's role/fit/sort choices.
   */
  private filterAndSortModels(models: AiModelInfo[], quickFilter: QuickFilter): AiModelInfo[] {
    let list = [...models];
    let order: 'asc' | 'desc' | null = null;

    switch (quickFilter) {
      case 'recommended':
        // Strongest CHAT model: must fit AND be able to call the financial
        // tools (M52 — a photo model must never top the chat recommendation).
        list = list.filter(
          (m) =>
            (m.role === 'main' || m.role === 'both') &&
            Boolean(m.tool_parser) &&
            this.fitOf(m) !== 'no' &&
            !LEGACY_FAMILIES.test(m.id),
        );
        order = 'desc';
        break;
      case 'all-in-one':
        // The rare model that genuinely does BOTH photos and tool calling.
        list = list.filter(
          (m) =>
            m.supports_vision &&
            Boolean(m.tool_parser) &&
            this.fitOf(m) !== 'no' &&
            !LEGACY_FAMILIES.test(m.id),
        );
        order = 'desc';
        break;
      case 'vision-big':
        list = list.filter((m) => m.supports_vision && this.fitOf(m) !== 'no');
        order = 'desc';
        break;
      case 'vision-small':
        list = list.filter((m) => m.supports_vision && this.fitOf(m) !== 'no');
        order = 'asc';
        break;
      case 'finance':
        // Financial reasoning needs tool calling; strongest tool-capable first.
        list = list.filter(
          (m) =>
            (m.role === 'main' || m.role === 'both') &&
            Boolean(m.tool_parser) &&
            !LEGACY_FAMILIES.test(m.id),
        );
        order = 'desc';
        break;
      case 'all':
        break;
    }

    const role = this.roleFilter();
    if (role === 'main') {
      list = list.filter((m) => m.role === 'main' || m.role === 'both');
    } else if (role === 'vision') {
      list = list.filter((m) => m.supports_vision);
    }
    if (this.onlyFits()) {
      list = list.filter((m) => this.fitOf(m) === 'fits' || this.fitOf(m) === 'tight');
    }

    const sort = this.sortBy();
    if (sort === 'size-desc') {
      order = 'desc';
    } else if (sort === 'size-asc') {
      order = 'asc';
    }
    if (sort === 'memory-asc') {
      list.sort((a, b) => (a.est_memory_gb || 1e9) - (b.est_memory_gb || 1e9));
    } else if (order === 'desc') {
      // M71 generation-aware: modern releases first (largest first), older
      // generations after — a 2024-era 110B must not outrank a modern 80B.
      list.sort(
        (a, b) =>
          Number(this.isModern(b)) - Number(this.isModern(a)) ||
          b.parameters_b - a.parameters_b ||
          b.est_memory_gb - a.est_memory_gb,
      );
    } else if (order === 'asc') {
      // Unknown sizes (0) sort last, not first.
      list.sort((a, b) => (a.parameters_b || 1e9) - (b.parameters_b || 1e9));
    }
    return list;
  }

  /** Complete filtered search sequence — the count line reads this. */
  protected readonly searchResultCount = computed(() => this.filteredSearchModels().length);

  /**
   * The submitted query's ordered, displayable rows (#168): candidates in
   * stable order (HF response, then curated, then matching stubs), query
   * matching on id/label substring, deployment eligibility, the shared
   * facets/sort, and finally the exact-first pin.
   */
  private readonly filteredSearchModels = computed<AiModelInfo[]>(() => {
    const submitted = this.submittedQuery();
    if (submitted === null) {
      return [];
    }
    const normalized = submitted.trim().toLowerCase();
    if (!normalized) {
      return [];
    }

    // Curated metadata wins at the candidate's first position: the merge keeps
    // the HF record's position, then the map swaps in the curated record.
    const curated = this.safeValue(this.catalog) ?? [];
    const merged = this.mergeModels(this.searchResults(), curated);
    const curatedById = new Map(curated.map((m) => [m.id.toLowerCase(), m] as const));
    const candidates = merged.map((m) => curatedById.get(m.id.toLowerCase()) ?? m);

    // Query matching: literal id/label substring. Stubs join only by ID —
    // their generated "(not in catalog)" label must never match alone.
    const matches = (m: AiModelInfo) =>
      m.id.toLowerCase().includes(normalized) || m.label.toLowerCase().includes(normalized);
    const known = candidates.filter(matches);
    const knownIds = new Set(candidates.map((m) => m.id.toLowerCase()));
    const stubs = this.activeStubs().filter(
      (s) => s.id.toLowerCase().includes(normalized) && !knownIds.has(s.id.toLowerCase()),
    );

    // Eligibility: without the cluster, min_nodes >= 2 entries are out; an
    // oversized min_nodes == 1 model stays with its honest single-box verdict.
    const eligible = [...known, ...stubs].filter((m) =>
      this.clusterActive() ? true : !this.isClusterModel(m),
    );

    // Default sort preserves candidate order (HF order, then curated, then
    // stubs); explicit sorts reuse the shared comparators.
    const ranked = this.filterAndSortModels(eligible, 'all');

    // Exact-first pin: the single case-insensitive full-ID equality match —
    // only if it survived eligibility and facets — moves to index 0, overriding
    // explicit sorts. A filter-excluded match is never reinserted.
    const exactIndex = ranked.findIndex((m) => m.id.toLowerCase() === normalized);
    if (exactIndex > 0) {
      const [exact] = ranked.splice(exactIndex, 1);
      ranked.unshift(exact);
    }
    return ranked;
  });

  /** Mode-aware pagination (#168): search mode paginates the search list. */
  protected readonly visibleModels = computed(() =>
    (this.searchActive() ? this.filteredSearchModels() : this.filteredModels()).slice(
      0,
      this.visibleCount(),
    ),
  );
  protected readonly hiddenCount = computed(() => {
    const total = this.searchActive()
      ? this.filteredSearchModels().length
      : this.filteredModels().length;
    return Math.max(0, total - this.visibleModels().length);
  });

  protected showMore(): void {
    this.visibleCount.update((count) => count + PAGE_SIZE);
  }

  /** M52: guided pairing — after picking a chat model, go choose the photo model. */
  protected pickPhotoModelNext(): void {
    this.expandedId.set(null);
    void this.setQuickFilter('vision-big');
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
        apiErrorMessage(
          error,
          $localize`:Error message|The photo model swap request failed to start:Could not start the vision swap. Is the model manager running?`,
        ),
      );
      return;
    }
    this.lastAppliedMain.set(main);
    this.applyState.set(data);
    this.startPolling();
  }

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      // #168: a pending fetch must never commit after destruction.
      this.destroyed = true;
      this.liveGeneration += 1;
      this.stopPolling();
    });
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
    // #168: cluster availability re-partitions both lists in place — the
    // submitted query and fetched candidates stay; the window resets.
    effect(() => {
      this.clusterActive();
      this.resetModelWindow();
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

    // The PUT replaces the whole config — carry the saved cluster toggle along
    // so an Advanced save never silently disables clustering (ADR 0071).
    const { error } = await this.api.updateAiRuntimeConfig({
      ...this.form.getRawValue(),
      cluster_enabled: this.config.value()?.cluster_enabled ?? false,
    });

    this.submitting.set(false);

    if (error) {
      this.submitError.set(
        apiErrorMessage(
          error,
          $localize`:Error message|The AI runtime configuration could not be saved:Failed to update AI runtime configuration.`,
        ),
      );
      return;
    }

    this.submitSuccess.set(true);
    this.config.reload();
  }
}
