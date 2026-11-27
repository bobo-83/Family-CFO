import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { AiRuntime } from './ai-runtime';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

/** A #170 apply-job status with job identity and sane defaults. */
function jobData(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 'job-1',
    request_id: 'req-1',
    sequence: 1,
    state: 'running',
    lifecycle_state: 'accepted',
    main_model: 'Qwen/Qwen2.5-32B-Instruct',
    vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
    log_tail: '',
    requested_selection: {
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
      photo_mode: 'separate',
      topology: 'auto',
    },
    created_at: '2026-11-21T00:00:00Z',
    updated_at: '2026-11-21T00:00:00Z',
    recovery_available: false,
    ...overrides,
  };
}

/** One macrotask turn — lets the constructor's floating resume fetch settle. */
const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const CATALOG = [
  {
    id: 'Qwen/Qwen2.5-32B-Instruct',
    label: 'Qwen2.5 32B',
    role: 'main',
    parameters_b: 32,
    est_memory_gb: 65,
    est_disk_gb: 62,
    tool_parser: 'hermes',
    supports_vision: false,
    gated: false,
    notes: '',
  },
  {
    id: 'Qwen/Qwen2.5-VL-32B-Instruct',
    label: 'Qwen2.5-VL 32B',
    role: 'both',
    parameters_b: 33,
    est_memory_gb: 70,
    est_disk_gb: 66,
    tool_parser: null,
    supports_vision: true,
    gated: false,
    notes: '',
  },
  {
    id: 'Qwen/Qwen2.5-VL-7B-Instruct',
    label: 'Qwen2.5-VL 7B',
    role: 'vision',
    parameters_b: 7,
    est_memory_gb: 16,
    est_disk_gb: 16,
    tool_parser: null,
    supports_vision: true,
    gated: false,
    notes: '',
  },
];

describe('AiRuntime', () => {
  let apiMock: {
    getAiRuntimeConfig: ReturnType<typeof vi.fn>;
    updateAiRuntimeConfig: ReturnType<typeof vi.fn>;
    getAiRuntimeStatus: ReturnType<typeof vi.fn>;
    listAiModels: ReturnType<typeof vi.fn>;
    getAiHardwareProfile: ReturnType<typeof vi.fn>;
    searchAiModels: ReturnType<typeof vi.fn>;
    getAiModelDetail: ReturnType<typeof vi.fn>;
    applyAiModelSelection: ReturnType<typeof vi.fn>;
    getAiApplyStatus: ReturnType<typeof vi.fn>;
    retryAiApplyRecovery: ReturnType<typeof vi.fn>;
    getAiStudyStatus: ReturnType<typeof vi.fn>;
    getAiUsage: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    apiMock = {
      getAiRuntimeConfig: vi.fn().mockResolvedValue(
        response({
          provider: 'vllm',
          base_url: 'http://vllm:8000',
          model: 'Qwen/Qwen2.5-32B-Instruct',
          enabled: true,
        }),
      ),
      updateAiRuntimeConfig: vi.fn(),
      getAiRuntimeStatus: vi.fn().mockResolvedValue(
        response({
          enabled: true,
          provider: 'vllm',
          model: 'Qwen/Qwen2.5-32B-Instruct',
          ready: true,
          served_model: 'Qwen/Qwen2.5-32B-Instruct',
          detail: 'ok',
          vision_ready: true,
          vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
          vision_enabled: true,
        }),
      ),
      listAiModels: vi.fn().mockResolvedValue(response({ models: CATALOG })),
      getAiHardwareProfile: vi.fn().mockResolvedValue(
        response({
          gpu_memory_gb: null,
          system_memory_gb: 120,
          disk_free_gb: 500,
          source: 'system',
        }),
      ),
      searchAiModels: vi.fn().mockResolvedValue(response({ models: [] })),
      getAiModelDetail: vi.fn().mockResolvedValue(response({ info: { id: 'x' } })),
      applyAiModelSelection: vi.fn(),
      // #170: the explicit idle read (no job admitted) — the constructor's
      // screen-entry lookup resolves to this by default.
      getAiApplyStatus: vi.fn().mockResolvedValue(
        response({
          job_id: null,
          request_id: null,
          sequence: null,
          state: 'idle',
          lifecycle_state: null,
          log_tail: '',
          requested_selection: null,
          created_at: null,
          updated_at: null,
          recovery_available: false,
        }),
      ),
      retryAiApplyRecovery: vi.fn(),
      getAiStudyStatus: vi.fn().mockResolvedValue(
        response({
          total_months: 17,
          studied_months: 14,
          coverage_percent: 82,
          last_studied_at: '2026-07-19T02:10:00Z',
          runtime_usable: true,
          insights: [
            {
              key: 'grocery_spending_pattern',
              value: 'Groceries run about $1,240 a month.',
              updated_at: '2026-07-19T02:10:00Z',
            },
          ],
        }),
      ),
      getAiUsage: vi.fn().mockResolvedValue(
        response({
          households: [
            {
              household_id: 'hh-cedar',
              name: 'Cedar family',
              chats_24h: 5,
              chats_7d: 12,
              median_answer_ms: 8300,
              storage_bytes: 1_200_000_000,
            },
            {
              household_id: 'hh-birch',
              name: 'Birch family',
              chats_24h: 0,
              chats_7d: 1,
              median_answer_ms: null,
              storage_bytes: 340_000_000,
            },
          ],
          chat_hourly_limit: 30,
        }),
      ),
    };
  });

  async function create(role = 'owner') {
    TestBed.configureTestingModule({
      imports: [AiRuntime],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock(role) },
      ],
    });
    const fixture = TestBed.createComponent(AiRuntime);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // --- Serving plans (M48): what an apply would actually run -----------------

  it('a photo-blind main keeps the current photo model in the plan and fit', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    const plan = component['planFor'](CATALOG[0] as never);

    expect(plan.mainId).toBe('Qwen/Qwen2.5-32B-Instruct');
    expect(plan.visionId).toBe('Qwen/Qwen2.5-VL-7B-Instruct'); // kept from status
    // (65 + 16) * 1.15 ≈ 93 vs 120 GB budget.
    expect(plan.memoryGb).toBe(93);
    expect(plan.memoryVerdict).toBe('fits');
  });

  it('a vision-capable main replaces both models', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    const plan = component['planFor'](CATALOG[1] as never);

    expect(plan.mainId).toBe('Qwen/Qwen2.5-VL-32B-Instruct');
    expect(plan.visionId).toBeNull();
    expect(plan.memoryGb).toBe(Math.round(70 * 1.15));
  });

  it('a vision model pairs with the current chat model', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    const plan = component['planFor'](CATALOG[2] as never);

    expect(plan.mainId).toBe('Qwen/Qwen2.5-32B-Instruct'); // current served main
    expect(plan.visionId).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
  });

  it('flags a plan that will not fit the hardware', async () => {
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({ gpu_memory_gb: 24, system_memory_gb: 64, disk_free_gb: 30, source: 'env' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    const plan = component['planFor'](CATALOG[0] as never);
    expect(plan.memoryVerdict).toBe('no'); // 93 GB vs 24 GB GPU
    expect(plan.diskVerdict).toBe('no'); // 78 GB weights vs 30 GB free
  });

  // --- Apply from an expanded row (M48) ---------------------------------------

  it('applyModel sends one explicit selection and observes the job to success', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['applyModel'](CATALOG[0] as never);
    // Photo-blind main: the current photo model is kept EXPLICITLY, topology
    // is the server's to resolve, and one request_id rides the confirmation.
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
      photo_mode: 'separate',
      topology: 'auto',
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    expect(component['applyPhase']()).toBe('preparing');
    expect(component['applyJob']()?.job_id).toBe('job-1');

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'verifying', sequence: 2 })),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('replacing');

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'succeeded', state: 'succeeded', sequence: 3 })),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('succeeded');
    // Terminal: observation ended — a further poll cannot restart it.
    expect(component['observationJobId']).toBeNull();
  });

  it('a terminal job refreshes the runtime config and live status (#170)', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);
    apiMock.getAiRuntimeConfig.mockClear();
    apiMock.getAiRuntimeStatus.mockClear();

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'succeeded', state: 'succeeded', sequence: 3 })),
    );
    await component['pollOnce']();
    await fixture.whenStable();

    expect(apiMock.getAiRuntimeConfig).toHaveBeenCalled();
    expect(apiMock.getAiRuntimeStatus).toHaveBeenCalled();
  });

  it('surfaces a failed replacement as an alert with its safe cause', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(
        jobData({
          lifecycle_state: 'failed',
          state: 'failed',
          sequence: 2,
          reason_code: 'insufficient_memory',
          message: 'The model needs more memory than the box has.',
        }),
      ),
    );
    await component['pollOnce']();
    fixture.detectChanges();
    expect(component['applyPhase']()).toBe('failed');
    expect(component['applyFailureDetail']()).toBe(
      'The model needs more memory than the box has.',
    );
    const alert = (fixture.nativeElement as HTMLElement).querySelector(
      '.apply-live[role="alert"]',
    );
    expect(alert?.textContent).toContain('failed before anything was changed');
  });

  it('shows the restored state after a failed replacement recovers', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(
        jobData({
          lifecycle_state: 'restored',
          state: 'failed', // legacy coarse projection
          sequence: 6,
          reason_code: 'verification_failed',
        }),
      ),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('restored');
    // Terminal — new applies are allowed again.
    expect(component['replacementInFlight']()).toBe(false);
  });

  it('offers Retry recovery for recovery_failed and re-observes the new attempt', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(
        jobData({
          lifecycle_state: 'recovery_failed',
          state: 'failed',
          sequence: 5,
          reason_code: 'restore_failed',
          message: 'The previous runtime failed to come back.',
          recovery_available: true,
        }),
      ),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('recovery');
    expect(component['replacementInFlight']()).toBe(false);

    apiMock.retryAiApplyRecovery.mockResolvedValue(
      response(jobData({ lifecycle_state: 'restoring', sequence: 6 })),
    );
    await component['retryRecovery']();
    expect(apiMock.retryAiApplyRecovery).toHaveBeenCalledWith('job-1', {
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    // The returned job replaced the observation target.
    expect(component['applyPhase']()).toBe('restoring');
    expect(component['observationJobId']).toBe('job-1');
  });

  it('polls accept only the observed job with an increasing sequence', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 7 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);
    expect(component['applyJob']()?.sequence).toBe(7);

    // Same sequence (no server-side change) — dropped.
    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'launching', sequence: 7 })),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('preparing');

    // A different job's status can never replace the observed one.
    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ job_id: 'job-other', lifecycle_state: 'succeeded', state: 'succeeded', sequence: 99 })),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('preparing');

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'launching', sequence: 8 })),
    );
    await component['pollOnce']();
    expect(component['applyPhase']()).toBe('replacing');
    component['stopObservation']();
  });

  it('a thrown network failure is transient: the job is retained, the loop continues', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);

    apiMock.getAiApplyStatus.mockRejectedValue(new Error('offline'));
    await component['pollOnce']();
    expect(component['applyPollError']()).toBeTruthy();
    expect(component['applyJob']()?.lifecycle_state).toBe('accepted');

    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'staging', sequence: 2 })),
    );
    await component['pollOnce']();
    expect(component['applyPollError']()).toBeNull();
    component['stopObservation']();
  });

  it('at most one poll is in flight; overlapping ticks are dropped', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    component['beginObservation'](jobData({ lifecycle_state: 'accepted', sequence: 1 }) as never);
    let release!: (value: unknown) => void;
    apiMock.getAiApplyStatus.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const first = component['pollOnce']();
    const second = component['pollOnce']();
    await tick();
    release(response(jobData({ lifecycle_state: 'launching', sequence: 2 })));
    await first;
    await second;
    // The constructor's entry lookup plus exactly ONE poll.
    expect(apiMock.getAiApplyStatus).toHaveBeenCalledTimes(2);
    component['stopObservation']();
  });

  it('screen entry adopts a running job and resumes observation (#170)', async () => {
    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'verifying', sequence: 4 })),
    );
    const fixture = await create();
    await tick();
    const component = fixture.componentInstance;
    expect(component['applyPhase']()).toBe('replacing');
    expect(component['observationJobId']).toBe('job-1');
    component['stopObservation']();
  });

  it('screen entry shows a terminal job without polling it (#170)', async () => {
    apiMock.getAiApplyStatus.mockResolvedValue(
      response(
        jobData({
          lifecycle_state: 'recovery_failed',
          state: 'failed',
          sequence: 9,
          recovery_available: true,
        }),
      ),
    );
    const fixture = await create();
    await tick();
    const component = fixture.componentInstance;
    expect(component['applyPhase']()).toBe('recovery');
    apiMock.getAiApplyStatus.mockClear();
    await tick();
    // No observation loop was started for a terminal job.
    expect(apiMock.getAiApplyStatus).not.toHaveBeenCalled();
  });

  it('a name-derived "too big" estimate still allows the apply request (#170)', async () => {
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({ gpu_memory_gb: 24, system_memory_gb: 64, disk_free_gb: 30, source: 'env' }),
    );
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    // VL-32B is not the served model and its 70 GB estimate is far over the
    // 24 GB budget — the badge says "no" while Apply stays enabled.
    expect(component['fitOf'](CATALOG[1] as never)).toBe('no');

    // 'all' — the default recommended filter hides tool-less models.
    component['quickFilter'].set('all');
    component['toggleExpand']('Qwen/Qwen2.5-VL-32B-Instruct');
    await fixture.whenStable();
    fixture.detectChanges();
    const applyButton = (
      fixture.nativeElement as HTMLElement
    ).querySelector<HTMLButtonElement>('.model-detail__actions .picker__apply');
    expect(applyButton).toBeTruthy();
    expect(applyButton!.disabled).toBe(false);

    await component['applyModel'](CATALOG[1] as never);
    expect(apiMock.applyAiModelSelection).toHaveBeenCalled();
    component['stopObservation']();
  });

  it('blocks Advanced writes while a replacement is unfinished (#170)', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'verifying', sequence: 2 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);
    expect(component['replacementInFlight']()).toBe(true);

    await component['submit']();
    await component['setClusterEnabled'](true);
    component['visionModelInput'].set('Qwen/Qwen2.5-VL-3B-Instruct');
    await component['applyVisionModel']();
    expect(apiMock.updateAiRuntimeConfig).not.toHaveBeenCalled();
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledTimes(1);

    // Terminal: writes are allowed again.
    apiMock.getAiApplyStatus.mockResolvedValue(
      response(jobData({ lifecycle_state: 'succeeded', state: 'succeeded', sequence: 3 })),
    );
    await component['pollOnce']();
    expect(component['replacementInFlight']()).toBe(false);
  });

  it('Apply never resets the #168 browse/search state', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({ models: [CATALOG[1]] }),
    );
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['searchQuery'].set('VL-32B');
    await component['runSearch']();
    expect(component['submittedQuery']()).toBe('VL-32B');
    const resultsBefore = component['filteredSearchModels']().length;
    expect(resultsBefore).toBeGreaterThan(0);

    await component['applyModel'](CATALOG[0] as never);

    expect(component['submittedQuery']()).toBe('VL-32B');
    expect(component['filteredSearchModels']().length).toBe(resultsBefore);
    component['stopObservation']();
  });

  it('toggles row expansion', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    expect(component['expandedId']()).toBeNull();
    component['toggleExpand']('a');
    expect(component['expandedId']()).toBe('a');
    component['toggleExpand']('a');
    expect(component['expandedId']()).toBeNull();
  });

  // --- Live quick filters (M48) ------------------------------------------------

  it('quick filters fetch a live list from Hugging Face', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['setQuickFilter']('vision-big');
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      pipeline: 'image-text-to-text',
      deep: true,
      limit: 20,
    });

    await component['setQuickFilter']('finance');
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      q: 'finance',
      pipeline: 'text-generation',
      deep: true,
      limit: 20,
    });

    await component['setQuickFilter']('recommended');
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      pipeline: 'text-generation',
      deep: true,
      limit: 20,
    });
  });

  it('recommended keeps only fitting mains, strongest first, live results merged', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'big/Huge-200B-Instruct',
            label: 'Huge 200B',
            role: 'main',
            parameters_b: 200,
            est_memory_gb: 400,
            est_disk_gb: 380,
            tool_parser: 'hermes',
            supports_vision: false,
            gated: false,
            notes: '',
          },
          {
            id: 'neat/Solid-40B-Instruct',
            label: 'Solid 40B',
            role: 'main',
            parameters_b: 40,
            est_memory_gb: 82,
            est_disk_gb: 78,
            tool_parser: 'hermes',
            supports_vision: false,
            gated: false,
            notes: '',
          },
        ],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['setQuickFilter']('recommended');
    const ids = component['filteredModels']().map((m) => m.id);
    expect(ids).not.toContain('big/Huge-200B-Instruct'); // 400 GB does not fit 120
    // M52: a photo model that can't call tools must never top the CHAT list.
    expect(ids).not.toContain('Qwen/Qwen2.5-VL-32B-Instruct');
    expect(ids[0]).toBe('neat/Solid-40B-Instruct'); // strongest fitting, from the live list
  });

  it('recommended excludes known-legacy families (M54)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'Qwen/Qwen1.5-110B-Chat-AWQ',
            label: 'Qwen1.5 110B AWQ',
            role: 'main',
            parameters_b: 110,
            est_memory_gb: 72,
            est_disk_gb: 68,
            tool_parser: 'hermes',
            supports_vision: false,
            gated: false,
            notes: '',
          },
        ],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['setQuickFilter']('recommended');
    const ids = component['filteredModels']().map((m) => m.id);
    // A 2024-era 110B must not outrank modern models in the ranked list...
    expect(ids).not.toContain('Qwen/Qwen1.5-110B-Chat-AWQ');
    // ...but "All" still shows it.
    component['setQuickFilter']('all');
    expect(component['filteredModels']().map((m) => m.id)).toContain('Qwen/Qwen1.5-110B-Chat-AWQ');
  });

  it('all-in-one lists only dual-capable models, honest empty state otherwise', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    // Today's catalog has no model with BOTH vision and tool calling.
    await component['setQuickFilter']('all-in-one');
    expect(component['filteredModels']().length).toBe(0);

    // A future dual-capable model appears automatically.
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'future/Dual-30B-VL-Instruct',
            label: 'Dual 30B',
            role: 'both',
            parameters_b: 30,
            est_memory_gb: 62,
            est_disk_gb: 58,
            tool_parser: 'hermes',
            supports_vision: true,
            gated: false,
            notes: '',
          },
        ],
      }),
    );
    await component['setQuickFilter']('all-in-one');
    expect(component['filteredModels']().map((m) => m.id)).toEqual(['future/Dual-30B-VL-Instruct']);
  });

  it('guided pairing jumps to the vision filter and collapses the row', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    component['toggleExpand']('Qwen/Qwen2.5-32B-Instruct');
    component['pickPhotoModelNext']();
    expect(component['expandedId']()).toBeNull();
    expect(component['quickFilter']()).toBe('vision-big');
  });

  it('vision quick filters sort vision-capable models by size', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['setQuickFilter']('vision-big');
    let models = component['filteredModels']();
    expect(models.every((m) => m.supports_vision)).toBe(true);
    expect(models[0].id).toBe('Qwen/Qwen2.5-VL-32B-Instruct');

    await component['setQuickFilter']('vision-small');
    models = component['filteredModels']();
    expect(models[0].id).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
  });

  it('falls back to curated models when Hugging Face is unreachable', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response(undefined, { error: { code: 'http_error', message: 'offline' } }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['setQuickFilter']('recommended');
    expect(component['searchError']()).toBeTruthy();
    // Curated fitting mains still listed.
    const ids = component['filteredModels']().map((m) => m.id);
    expect(ids).toContain('Qwen/Qwen2.5-32B-Instruct');
  });

  // --- M49: honest "biggest vision that fits" ----------------------------------

  it('vision-big uses the deep server fan-out and the largest fitting model tops', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    apiMock.searchAiModels.mockClear();
    // M53: the size-hinted fan-out happens SERVER-side (deep=true) in one call.
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'popular/Small-8B-VL',
            label: 'Small 8B VL',
            role: 'both',
            parameters_b: 8,
            est_memory_gb: 17,
            est_disk_gb: 16,
            tool_parser: null,
            supports_vision: true,
            gated: false,
            notes: '',
          },
          {
            id: 'big/Giant-72B-VL-AWQ',
            label: 'Giant 72B VL AWQ',
            role: 'both',
            parameters_b: 72,
            est_memory_gb: 47,
            est_disk_gb: 44,
            tool_parser: null,
            supports_vision: true,
            gated: false,
            notes: '',
          },
        ],
      }),
    );

    await component['setQuickFilter']('vision-big');
    expect(apiMock.searchAiModels).toHaveBeenCalledTimes(1);
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      pipeline: 'image-text-to-text',
      deep: true,
      limit: 20,
    });
    // 72B AWQ (fits: 47*1.15 < 120) tops the list.
    const ids = component['filteredModels']().map((m) => m.id);
    expect(ids[0]).toBe('big/Giant-72B-VL-AWQ');
    expect(ids).toContain('popular/Small-8B-VL');
  });

  it('vision-big drops models that do not fit the memory budget', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'huge/Colossal-90B-Vision',
            label: 'Colossal 90B Vision',
            role: 'both',
            parameters_b: 90,
            est_memory_gb: 189, // 189*1.15 > 120 budget
            est_disk_gb: 180,
            tool_parser: null,
            supports_vision: true,
            gated: false,
            notes: '',
          },
        ],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['setQuickFilter']('vision-big');
    const ids = component['filteredModels']().map((m) => m.id);
    expect(ids).not.toContain('huge/Colossal-90B-Vision');
  });

  it('stub estimates respect quantization markers in the name', async () => {
    apiMock.getAiRuntimeStatus.mockResolvedValue(
      response({
        enabled: true,
        provider: 'vllm',
        model: 'org/Fast-32B-Instruct-AWQ',
        ready: true,
        served_model: 'org/Fast-32B-Instruct-AWQ',
        detail: 'ok',
        vision_ready: false,
        vision_model: null,
        vision_enabled: false,
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    const stub = component['byId']('org/Fast-32B-Instruct-AWQ');
    // AWQ ~0.65 GB/B, not the bf16 2.1 that would double-count it.
    expect(stub!.est_memory_gb).toBe(Math.round(32 * 0.65));
  });

  // --- Stub synthesis: an off-catalog active model stays visible ---------------

  it('keeps an off-catalog served model visible via a synthesized stub', async () => {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'someorg/Custom-13B-Instruct',
        enabled: true,
      }),
    );
    apiMock.getAiRuntimeStatus.mockResolvedValue(
      response({
        enabled: true,
        provider: 'vllm',
        model: 'someorg/Custom-13B-Instruct',
        ready: true,
        served_model: 'someorg/Custom-13B-Instruct',
        detail: 'ok',
        vision_ready: false,
        vision_model: null,
        vision_enabled: false,
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    const stub = component['byId']('someorg/Custom-13B-Instruct');
    expect(stub).not.toBeNull();
    expect(stub!.label).toContain('not in catalog');
    expect(stub!.parameters_b).toBe(13); // estimated from the name
    // And its plan computes a real fit instead of disappearing.
    expect(component['planFor'](stub!).memoryGb).not.toBeNull();
  });

  // --- M51: tool-less vision models pair with the current main -----------------

  it('applyAsVision keeps the current chat model and swaps only photos', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['applyAsVision'](CATALOG[1] as never); // VL-32B, role both
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct', // current served main, unchanged
      vision_model: 'Qwen/Qwen2.5-VL-32B-Instruct',
      photo_mode: 'separate',
      topology: 'auto',
      request_id: expect.any(String),
    });
    component['stopObservation']();
  });

  it('a dual-capable main applies as ONE instance with photos routed to it', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['applyModel'](CATALOG[1] as never); // VL-32B, role both
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-VL-32B-Instruct',
      vision_model: null, // no separate describer — one instance
      photo_mode: 'main',
      topology: 'auto',
      request_id: expect.any(String),
    });
    component['stopObservation']();
  });

  // --- Advanced section ---------------------------------------------------------

  it('saves changes through the advanced form', async () => {
    apiMock.updateAiRuntimeConfig.mockResolvedValue(response({}));
    const fixture = await create();
    const component = fixture.componentInstance;
    expect(component['form'].value.base_url).toBe('http://vllm:8000');

    component['form'].patchValue({ model: 'llama-3-8b-instruct', enabled: true });
    await component['submit']();

    expect(apiMock.updateAiRuntimeConfig).toHaveBeenCalledWith({
      provider: 'vllm',
      base_url: 'http://vllm:8000',
      model: 'llama-3-8b-instruct',
      enabled: true,
      // ADR 0071: the full-object PUT preserves the saved cluster toggle.
      cluster_enabled: false,
    });
  });

  // --- Two-box cluster (ADR 0071) ---------------------------------------------

  it('shows cluster models under their heading only while the toggle is on', async () => {
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({
        gpu_memory_gb: null,
        system_memory_gb: 120,
        disk_free_gb: 500,
        source: 'system',
        cluster_peer_host: 'spark-2.local',
        cluster_peer_reachable: true,
        cluster_memory_gb: 238,
      }),
    );
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'Qwen/Qwen2.5-32B-Instruct',
        enabled: true,
        cluster_enabled: true,
      }),
    );
    apiMock.listAiModels.mockResolvedValue(
      response({
        models: [
          ...CATALOG,
          {
            id: 'Qwen/Qwen3-235B-A22B-Instruct',
            label: 'Qwen3 235B MoE',
            role: 'main',
            parameters_b: 235,
            est_memory_gb: 180,
            est_disk_gb: 170,
            tool_parser: 'hermes',
            supports_vision: false,
            min_nodes: 2,
            gated: false,
            notes: '',
          },
          {
            // min_nodes 1, but too big for the 120 GB box alone: with the
            // cluster active it must be OFFERED as a cluster deployment.
            id: 'Qwen/Qwen2.5-72B-Instruct',
            label: 'Qwen2.5 72B',
            role: 'main',
            parameters_b: 72,
            est_memory_gb: 145,
            est_disk_gb: 140,
            tool_parser: 'hermes',
            supports_vision: false,
            gated: false,
            notes: '',
          },
        ],
      }),
    );
    const fixture = await create();
    const host = fixture.nativeElement as HTMLElement;

    // Toggle on + peer reachable: the section renders with the model and the
    // COMBINED budget, and the model never leaks into the single-box list.
    const section = host.querySelector('.cluster-models');
    expect(section).toBeTruthy();
    expect(section!.querySelector('.cluster-models__heading')?.textContent).toContain(
      'Cluster (both boxes)',
    );
    expect(section!.textContent).toContain('Qwen3 235B MoE');
    expect(section!.textContent).toContain('238 GB combined');
    expect(
      host.querySelector('.cluster__line')?.textContent?.replace(/\s+/g, ' ').trim(),
    ).toContain('Second box detected (spark-2.local) — combined budget 238 GB');
    const component = fixture.componentInstance;
    // "All" so exclusions below prove the cluster gating, not the fit filter.
    component['quickFilter'].set('all');
    expect(component['filteredModels']().map((m) => m.id)).not.toContain(
      'Qwen/Qwen3-235B-A22B-Instruct',
    );

    // Derived gating: the big min_nodes==1 model moves into the cluster
    // section — 145*1.15 GB fits the combined 238 — and leaves the single list.
    expect(section!.textContent).toContain('Qwen2.5 72B');
    expect(section!.textContent).toContain('Fits across both boxes');
    const big = component['byId']('Qwen/Qwen2.5-72B-Instruct')!;
    expect(component['clusterModels']().map((m) => m.id)).toContain(big.id);
    expect(component['fitOf'](big)).toBe('fits');
    expect(component['filteredModels']().map((m) => m.id)).not.toContain(big.id);

    // #170: applying it from the cluster section sends topology 'auto' — the
    // server resolves the two-node plan from the household's consent; the
    // deprecated cluster flag is never sent. The response is terminal so the
    // follow-on cluster toggle below is not blocked by an unfinished swap.
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'succeeded', state: 'succeeded', sequence: 1 })),
    );
    await component['applyModel'](big);
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-72B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct', // current photo model kept
      photo_mode: 'separate',
      topology: 'auto',
      request_id: expect.any(String),
    });
    component['stopObservation']();

    // Toggle off: the section disappears.
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'Qwen/Qwen2.5-32B-Instruct',
        enabled: true,
        cluster_enabled: false,
      }),
    );
    component['config'].reload();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(host.querySelector('.cluster-models')).toBeFalsy();
    // Exactly today's single-box behavior: min_nodes >= 2 stays hidden, while
    // the too-big 72B is back in the single list with an honest verdict.
    const singleIds = component['filteredModels']().map((m) => m.id);
    expect(singleIds).not.toContain('Qwen/Qwen3-235B-A22B-Instruct');
    expect(singleIds).toContain('Qwen/Qwen2.5-72B-Instruct');
    expect(component['fitOf'](big)).toBe('no');
  });

  it('applies a vision model change from the Advanced section via the swap', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    expect(component['visionModelInput']()).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
    component['visionModelInput'].set('Qwen/Qwen2.5-VL-3B-Instruct');
    await component['applyVisionModel']();

    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-3B-Instruct',
      photo_mode: 'separate',
      topology: 'auto',
      request_id: expect.any(String),
    });
    component['stopObservation']();
  });

  it('clearing the Advanced vision field applies photos-off explicitly (#170)', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'accepted', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['visionModelInput'].set('');
    await component['applyVisionModel']();

    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: null,
      photo_mode: 'off',
      topology: 'auto',
      request_id: expect.any(String),
    });
    component['stopObservation']();
  });

  it('hides the configuration for a non-owner', async () => {
    const fixture = await create('adult');
    const form = (fixture.nativeElement as HTMLElement).querySelector('.ai-runtime-form');
    const picker = (fixture.nativeElement as HTMLElement).querySelector('.picker');
    expect(form).toBeFalsy();
    expect(picker).toBeFalsy();
  });

  // --- Household usage (#181) -------------------------------------------------

  it('renders household usage rows in server order with formatted values', async () => {
    const fixture = await create();
    const host = fixture.nativeElement as HTMLElement;

    const section = host.querySelector('.usage');
    expect(section).toBeTruthy();
    const rows = [...section!.querySelectorAll('.usage__row')].map((row) =>
      row.textContent!.replace(/\s+/g, ' ').trim(),
    );
    expect(rows.length).toBe(2);
    // Server pre-sorts heaviest first; the page must not reorder.
    expect(rows[0]).toContain('Cedar family');
    expect(rows[0]).toContain('12 chats this week (5 in the last 24 h)');
    expect(rows[0]).toContain('8.3s median'); // same formatter as the medians
    expect(rows[0]).toContain('1.2 GB');
    expect(rows[1]).toContain('Birch family');
    expect(rows[1]).toContain('1 chat this week (0 in the last 24 h)');
    expect(rows[1]).toContain('— median'); // no samples yet
    expect(rows[1]).toContain('340 MB');
    expect(section!.querySelector('.usage__cap')?.textContent).toContain(
      'Fair-use cap: 30 chats/hour per household',
    );
  });

  it('hides household usage for a single household with the cap off', async () => {
    apiMock.getAiUsage.mockResolvedValue(
      response({
        households: [
          {
            household_id: 'hh-cedar',
            name: 'Cedar family',
            chats_24h: 2,
            chats_7d: 9,
            median_answer_ms: 4200,
            storage_bytes: 90_000_000,
          },
        ],
        chat_hourly_limit: 0,
      }),
    );
    const fixture = await create();
    expect((fixture.nativeElement as HTMLElement).querySelector('.usage')).toBeFalsy();
  });

  it('shows household usage for a single household while the cap is armed', async () => {
    apiMock.getAiUsage.mockResolvedValue(
      response({
        households: [
          {
            household_id: 'hh-cedar',
            name: 'Cedar family',
            chats_24h: 2,
            chats_7d: 9,
            median_answer_ms: 4200,
            storage_bytes: 90_000_000,
          },
        ],
        chat_hourly_limit: 12,
      }),
    );
    const fixture = await create();
    const section = (fixture.nativeElement as HTMLElement).querySelector('.usage');
    expect(section).toBeTruthy();
    expect(section!.querySelector('.usage__cap')?.textContent).toContain(
      'Fair-use cap: 12 chats/hour per household',
    );
  });

  it('hides household usage when the endpoint answers 403 (telemetry fails quiet)', async () => {
    apiMock.getAiUsage.mockResolvedValue(
      response(undefined, {
        error: { code: 'forbidden', message: 'AI runtime management required' },
      }),
    );
    const fixture = await create();
    expect((fixture.nativeElement as HTMLElement).querySelector('.usage')).toBeFalsy();
  });

  it('hides household usage for a member without the manage right', async () => {
    const fixture = await create('adult');
    expect((fixture.nativeElement as HTMLElement).querySelector('.usage')).toBeFalsy();
  });

  it('flags a household that needs a member sign-in and adds the footnote (#196)', async () => {
    apiMock.getAiUsage.mockResolvedValue(
      response({
        households: [
          {
            household_id: 'hh-cedar',
            name: 'Cedar family',
            chats_24h: 5,
            chats_7d: 12,
            median_answer_ms: 8300,
            storage_bytes: 1_200_000_000,
            member_key_ok: true,
          },
          {
            household_id: 'hh-birch',
            name: 'Birch family',
            chats_24h: 0,
            chats_7d: 1,
            median_answer_ms: null,
            storage_bytes: 340_000_000,
            member_key_ok: false,
          },
        ],
        chat_hourly_limit: 30,
      }),
    );
    const fixture = await create();
    const section = (fixture.nativeElement as HTMLElement).querySelector('.usage');
    const flags = [...section!.querySelectorAll('.usage__flag')];
    expect(flags.length).toBe(1); // only the household without a member key
    expect(flags[0].textContent).toContain('member sign-in needed');
    // The flag rides in the household's own row, not Cedar's.
    const birchRow = [...section!.querySelectorAll('.usage__row')].find((row) =>
      row.textContent!.includes('Birch family'),
    );
    expect(birchRow!.querySelector('.usage__flag')).toBeTruthy();
    expect(section!.querySelector('.usage__note')?.textContent).toContain(
      "can't be sealed until a member signs in",
    );
  });

  it('shows no member-sign-in marker or footnote when every household is ok (#196)', async () => {
    const fixture = await create(); // default mock: no member_key_ok false rows
    const section = (fixture.nativeElement as HTMLElement).querySelector('.usage');
    expect(section!.querySelector('.usage__flag')).toBeFalsy();
    expect(section!.querySelector('.usage__note')).toBeFalsy();
  });

  // --- Knowledge of your data (ADR 0040) -------------------------------------

  it('shows study coverage and insights to every member', async () => {
    const fixture = await create('adult'); // non-owner still sees it
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('.study')).toBeTruthy();
    expect(host.querySelector('.study__percent')?.textContent).toContain('82%');
    expect(host.querySelector('.study__months')?.textContent).toContain('Studied 14 of 17');
    expect(host.querySelector('.study__insights')?.textContent).toContain('Groceries run about');
  });

  it('flags studying as paused when no runtime is usable', async () => {
    apiMock.getAiStudyStatus.mockResolvedValue(
      response({
        total_months: 5,
        studied_months: 0,
        coverage_percent: 0,
        last_studied_at: null,
        runtime_usable: false,
        insights: [],
      }),
    );
    const fixture = await create();
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('.study__paused')?.textContent).toContain('Paused');
  });

  it('subtracts the resident vision slot from the budget and disables no-fit models (#182)', async () => {
    // 120 GB node with 22 GB claimed by the describer -> 98 GB budget: a
    // 100 GB estimate no longer fits.
    apiMock.getAiHardwareProfile = vi.fn().mockResolvedValue(
      response({
        gpu_memory_gb: null,
        system_memory_gb: 120,
        disk_free_gb: 500,
        source: 'system',
        vision_reserved_gb: 22,
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance as unknown as {
      memoryBudgetGb: () => number | null;
      fitOf: (m: { est_memory_gb: number; min_nodes?: number }) => string;
    };
    expect(component.memoryBudgetGb()).toBe(98);
    expect(component.fitOf({ est_memory_gb: 100, min_nodes: 1 })).toBe('no');
  });

  // --- #168: labeled search results view --------------------------------------

  const EXACT_ID = 'nvidia/Qwen3.8-Flash-Next-NVFP4';
  const exactModel = {
    id: EXACT_ID,
    label: 'Qwen3.8 Flash Next NVFP4',
    role: 'main',
    parameters_b: 80,
    est_memory_gb: 90,
    est_disk_gb: 85,
    tool_parser: 'hermes',
    supports_vision: false,
    gated: false,
    notes: '',
  };
  const suffixModel = {
    id: 'nvidia/Qwen3.8-Flash-Next-NVFP4-AWQ',
    label: 'Qwen3.8 Flash Next AWQ',
    role: 'main',
    parameters_b: 40,
    est_memory_gb: 55,
    est_disk_gb: 50,
    tool_parser: 'hermes',
    supports_vision: false,
    gated: false,
    notes: '',
  };
  const unrelatedModel = {
    id: 'meta/Llama-3.1-8B',
    label: 'Llama 3.1 8B',
    role: 'main',
    parameters_b: 8,
    est_memory_gb: 20,
    est_disk_gb: 18,
    tool_parser: 'hermes',
    supports_vision: false,
    gated: false,
    notes: '',
  };
  const clusterCurated = {
    id: 'big/Llama-3.3-70B',
    label: 'Llama 3.3 70B',
    role: 'main',
    parameters_b: 70,
    est_memory_gb: 140,
    est_disk_gb: 135,
    tool_parser: 'hermes',
    supports_vision: false,
    gated: false,
    notes: '',
    min_nodes: 2,
  };
  const clusterMatch = {
    id: 'org/Qwen3.8-Vision-200B',
    label: 'Qwen3.8 Vision 200B',
    role: 'main',
    parameters_b: 200,
    est_memory_gb: 200,
    est_disk_gb: 190,
    tool_parser: null,
    supports_vision: false,
    gated: false,
    notes: '',
    min_nodes: 2,
  };

  function deferred(): { promise: Promise<unknown>; resolve: (value: unknown) => void } {
    let resolve!: (value: unknown) => void;
    const promise = new Promise<unknown>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  }

  function statusResponse(data: unknown, status: number, error?: unknown) {
    return {
      data,
      error,
      request: new Request('http://localhost/'),
      response: new Response(null, { status }),
    } as never;
  }

  function enableCluster(): void {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'Qwen/Qwen2.5-32B-Instruct',
        enabled: true,
        cluster_enabled: true,
      }),
    );
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({
        gpu_memory_gb: null,
        system_memory_gb: 120,
        disk_free_gb: 500,
        source: 'system',
        cluster_peer_host: 'box2',
        cluster_peer_reachable: true,
        cluster_memory_gb: 240,
      }),
    );
  }

  function searchRowIds(fixture: { nativeElement: HTMLElement }): string[] {
    return [...fixture.nativeElement.querySelectorAll('.search-results .model-row__id code')].map(
      (node) => (node.textContent ?? '').trim(),
    );
  }

  it('ranks an exact owner/model first with its full id visible (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({ models: [suffixModel, unrelatedModel, exactModel] }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput'](`  ${EXACT_ID.toUpperCase()}  `);
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    // Labeled results heading uses the trimmed submitted query (case kept).
    const heading = (fixture.nativeElement as HTMLElement).querySelector('.search-results h3');
    expect(heading?.textContent).toContain(`Search results for "${EXACT_ID.toUpperCase()}"`);
    // Exact row first, full repo id visible while collapsed, unrelated gone.
    const rows = searchRowIds(fixture);
    expect(rows[0]).toBe(EXACT_ID);
    expect(rows).toContain('nvidia/Qwen3.8-Flash-Next-NVFP4-AWQ'); // suffix variant matches too
    expect(rows).not.toContain('meta/Llama-3.1-8B');
    // Mixed-case padded query matched case-insensitively; duplicates render once.
    expect(rows.filter((id) => id.toLowerCase() === EXACT_ID.toLowerCase()).length).toBe(1);
  });

  it('keeps the exact match first under an explicit sort and sends the query (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response({ models: [exactModel, suffixModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['setSortBy']('size-asc'); // smallest first would put the 40B suffix first
    component['onSearchInput'](EXACT_ID);
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(searchRowIds(fixture)[0]).toBe(EXACT_ID);
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      q: EXACT_ID,
      pipeline: 'any',
      limit: 20,
    });
  });

  it('does not reinsert an exact match excluded by the role facet (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response({ models: [exactModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['setRoleFilter']('vision');
    component['onSearchInput'](EXACT_ID);
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(searchRowIds(fixture)).toEqual([]);
    expect(host.querySelector('.search-results .page-empty')?.textContent).toContain(
      `No models match "${EXACT_ID}" with the current filters.`,
    );
  });

  it('keeps unrelated cluster recommendations out and keeps cluster fit + Apply (#168)', async () => {
    apiMock.listAiModels.mockResolvedValue(response({ models: [...CATALOG, clusterCurated] }));
    apiMock.searchAiModels.mockResolvedValue(
      response({ models: [clusterMatch, unrelatedModel, exactModel] }),
    );
    enableCluster();
    // Terminal apply response: the cluster toggle below must not be blocked
    // by an unfinished replacement (#170).
    apiMock.applyAiModelSelection.mockResolvedValue(
      response(jobData({ lifecycle_state: 'succeeded', state: 'succeeded', sequence: 1 })),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen3.8');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    // Only query-matching eligible rows render in one ordered list. A partial
    // query has no full-ID pin, so HF order governs; the unrelated curated
    // cluster recommendation stays out.
    const rows = searchRowIds(fixture);
    expect(rows).toEqual(['org/Qwen3.8-Vision-200B', EXACT_ID]);
    expect(rows).not.toContain('big/Llama-3.3-70B');
    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results')?.textContent).toContain('GB combined');

    // #170: the matching cluster row's Apply sends topology 'auto' — the
    // deprecated cluster flag is never sent from ordinary selections.
    const clusterRowEl = [...host.querySelectorAll('.search-results .model-row')].find((el) =>
      el.textContent?.includes('org/Qwen3.8-Vision-200B'),
    );
    const applyBtn = clusterRowEl?.querySelector('button.picker__apply') as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(false);
    applyBtn.click();
    await fixture.whenStable();
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith(
      expect.objectContaining({
        main_model: 'org/Qwen3.8-Vision-200B',
        topology: 'auto',
        request_id: expect.any(String),
      }),
    );
    expect(apiMock.applyAiModelSelection.mock.calls.at(-1)?.[0].cluster).toBeUndefined();
    component['stopObservation']();

    // Toggling the cluster off hides min_nodes >= 2 rows; the exact stays.
    // The PUT succeeds and the reload reads back the disabled config.
    const disabledConfig = response({
      provider: 'vllm',
      base_url: 'http://vllm:8000',
      model: 'Qwen/Qwen2.5-32B-Instruct',
      enabled: true,
      cluster_enabled: false,
    });
    apiMock.updateAiRuntimeConfig.mockResolvedValue(disabledConfig);
    apiMock.getAiRuntimeConfig.mockResolvedValue(disabledConfig);
    await component['setClusterEnabled'](false);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
  });

  it('hides cluster rows while the peer is unreachable (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response({ models: [clusterMatch, exactModel] }));
    enableCluster();
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({
        gpu_memory_gb: null,
        system_memory_gb: 120,
        disk_free_gb: 500,
        source: 'system',
        cluster_peer_host: 'box2',
        cluster_peer_reachable: false,
        cluster_memory_gb: 240,
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen3.8');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
  });

  it('shows a query-specific no-match state for a successful empty search (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response({ models: [] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('zzz-nonexistent');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results .page-empty')?.textContent).toContain(
      'No models match "zzz-nonexistent" with the current filters.',
    );
    expect(host.querySelector('.search-results .page-error')).toBeNull();
    expect(host.querySelectorAll('.search-results .model-row').length).toBe(0);
  });

  it('newest search wins regardless of completion order (#168)', async () => {
    const first = deferred();
    const second = deferred();
    apiMock.searchAiModels
      .mockImplementationOnce(() => first.promise as never)
      .mockImplementationOnce(() => second.promise as never);
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('llama'); // matches the older response's row
    void component['runSearch']();
    component['onSearchInput']('nvidia'); // matches only the newer response's row
    void component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['searching']()).toBe(true);

    second.resolve(response({ models: [exactModel] }));
    await fixture.whenStable();
    fixture.detectChanges();
    first.resolve(response({ models: [unrelatedModel] })); // older resolves LAST
    await fixture.whenStable();
    fixture.detectChanges();

    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
    expect(component['searching']()).toBe(false);
    expect(component['submittedQuery']()).toBe('nvidia');
  });

  it('a stale resolution while a newer search is pending commits nothing (#168)', async () => {
    const first = deferred();
    const second = deferred();
    apiMock.searchAiModels
      .mockImplementationOnce(() => first.promise as never)
      .mockImplementationOnce(() => second.promise as never);
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('llama');
    void component['runSearch']();
    component['onSearchInput']('nvidia');
    void component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    first.resolve(response({ models: [unrelatedModel] }));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['searching']()).toBe(true); // newer request still loading
    expect(searchRowIds(fixture)).toEqual([]); // stale commit discarded

    second.resolve(response({ models: [exactModel] }));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
  });

  it('a response arriving after clear cannot repopulate and browsing stays restored (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValueOnce(response({ models: [unrelatedModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['setQuickFilter']('recommended');

    const pending = deferred();
    apiMock.searchAiModels.mockImplementationOnce(() => pending.promise as never);
    component['onSearchInput']('qwen');
    void component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['searchActive']()).toBe(true);

    component['clearSearch']();
    fixture.detectChanges();
    expect(component['searchActive']()).toBe(false);
    expect(component['browseLiveResults']().map((m) => m.id)).toContain('meta/Llama-3.1-8B');

    pending.resolve(response({ models: [exactModel] }));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['searchActive']()).toBe(false);
    expect(searchRowIds(fixture)).toEqual([]);
  });

  it('dedupes an identical in-flight submission but accepts any other query (#168)', async () => {
    const pending = deferred();
    apiMock.searchAiModels.mockImplementation(() => pending.promise as never);
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen');
    void component['runSearch']();
    await fixture.whenStable();
    await component['runSearch'](); // identical while loading → no-op
    expect(apiMock.searchAiModels).toHaveBeenCalledTimes(1);

    component['onSearchInput']('QWEN'); // different identity (trim-only) → supersedes
    void component['runSearch']();
    expect(apiMock.searchAiModels).toHaveBeenCalledTimes(2);

    pending.resolve(response({ models: [] }));
    await fixture.whenStable();
  });

  it('renders dashboard-owned copy for a 503 failure and keeps matching local models (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      statusResponse(undefined, 503, {
        error: {
          code: 'http_error',
          message: 'Hugging Face is unreachable; showing curated models only',
        },
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen'); // matches all curated entries
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const alert = host.querySelector('.search-results .page-error');
    expect(alert?.textContent).toContain('Could not search Hugging Face. Search again to retry.');
    expect(alert?.textContent).not.toContain('showing curated models only');
    expect(host.querySelector('.search-results__fallback')?.textContent).toContain(
      'Showing matching catalog',
    );
    expect(searchRowIds(fixture).length).toBe(CATALOG.length);
  });

  it('shows no rows and no no-match message when a failed search has no local matches (#168)', async () => {
    apiMock.searchAiModels.mockRejectedValue(new Error('network down'));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('zzz-nothing');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results .page-error')?.textContent).toContain(
      'Could not search Hugging Face',
    );
    expect(host.querySelector('.search-results__fallback')).toBeNull();
    expect(host.querySelector('.search-results .page-empty')).toBeNull();
    expect(host.querySelectorAll('.search-results .model-row').length).toBe(0);
  });

  it('passes a structured non-503 failure message through (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      statusResponse(undefined, 401, { error: { code: 'http_error', message: 'Session expired' } }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.search-results .page-error')
        ?.textContent,
    ).toContain('Session expired');
  });

  it('treats a malformed payload as failure, not an empty success (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response(undefined));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results .page-error')).not.toBeNull();
    expect(host.querySelector('.search-results .page-empty')).toBeNull();
  });

  it('clears stale browse-live results on a browse fetch failure (D3, #168)', async () => {
    apiMock.searchAiModels.mockResolvedValueOnce(response({ models: [unrelatedModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['setQuickFilter']('recommended');
    expect(component['browseLiveResults']().length).toBe(1);

    apiMock.searchAiModels.mockResolvedValueOnce(
      statusResponse(undefined, 503, {
        error: {
          code: 'http_error',
          message: 'Hugging Face is unreachable; showing curated models only',
        },
      }),
    );
    await component['setQuickFilter']('finance');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component['browseLiveResults']()).toEqual([]);
    const host = fixture.nativeElement as HTMLElement;
    // Browse keeps the structured pass-through — the phrase is accurate there.
    expect(host.querySelector('.page-error')?.textContent).toContain('Hugging Face is unreachable');
    expect(component['visibleModels']().length).toBeGreaterThan(0); // curated fallback
  });

  it('still searches when the curated catalog fails to load (#168)', async () => {
    apiMock.listAiModels.mockRejectedValue(new Error('catalog down'));
    const fixture = await create();
    const component = fixture.componentInstance;
    apiMock.searchAiModels.mockResolvedValue(response({ models: [exactModel] }));

    component['onSearchInput'](EXACT_ID);
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.search-results .page-error'),
    ).toBeNull();
  });

  it('does not relabel submitted results while the draft changes (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(response({ models: [exactModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput'](EXACT_ID);
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    component['onSearchInput']('something else');
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results h3')?.textContent).toContain(EXACT_ID);
    expect(searchRowIds(fixture)).toEqual([EXACT_ID]);
  });

  it('restores the previous quick filter and browse results on clear without refetching (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValueOnce(response({ models: [unrelatedModel] }));
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['setQuickFilter']('recommended');
    const callsAfterBrowse = apiMock.searchAiModels.mock.calls.length;

    apiMock.searchAiModels.mockResolvedValueOnce(response({ models: [exactModel] }));
    component['onSearchInput']('nvidia');
    await component['runSearch']();
    await fixture.whenStable();
    expect(component['quickFilter']()).toBe('recommended'); // search never rewrites it

    component['clearSearch']();
    fixture.detectChanges();
    expect(component['quickFilter']()).toBe('recommended');
    expect(component['browseLiveResults']().map((m) => m.id)).toContain('meta/Llama-3.1-8B');
    expect(apiMock.searchAiModels.mock.calls.length).toBe(callsAfterBrowse + 1); // no clear-refetch
  });

  it('invalidates a pending browse fetch when All is selected (#168)', async () => {
    const pending = deferred();
    apiMock.searchAiModels.mockImplementationOnce(() => pending.promise as never);
    const fixture = await create();
    const component = fixture.componentInstance;

    void component['setQuickFilter']('recommended');
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['searching']()).toBe(true);

    await component['setQuickFilter']('all'); // no replacement fetch
    fixture.detectChanges();
    expect(component['searching']()).toBe(false);

    pending.resolve(response({ models: [unrelatedModel] }));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component['browseLiveResults']()).toEqual([]);
  });

  it('never commits after component destruction (#168)', async () => {
    const pending = deferred();
    apiMock.searchAiModels.mockImplementation(() => pending.promise as never);
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen');
    void component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    fixture.destroy();
    pending.resolve(response({ models: [exactModel] }));
    await fixture.whenStable();

    expect(component['searchResults']()).toEqual([]);
    expect(component['liveLoadState']()).toBe('loading');
  });

  it('paginates the single ordered search list across row kinds (#168)', async () => {
    enableCluster();
    const filler = Array.from({ length: 7 }, (_, i) => ({
      id: `lab/Qwen-Filler-${i}`,
      label: `Qwen filler ${i}`,
      role: 'main',
      parameters_b: 3 + i,
      est_memory_gb: 8 + i,
      est_disk_gb: 7 + i,
      tool_parser: 'hermes',
      supports_vision: false,
      gated: false,
      notes: '',
    }));
    apiMock.searchAiModels.mockResolvedValue(
      response({ models: [clusterMatch, ...filler, exactModel] }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('qwen');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    // 12 matching rows (9 HF + the 3 curated Qwen entries, all matching the
    // query) — cluster and single-box rows share one paginated list.
    expect(component['searchResultCount']()).toBe(12);
    expect(component['visibleModels']().length).toBe(6);
    expect(component['hiddenCount']()).toBe(6);
    expect(searchRowIds(fixture)[0]).toBe('org/Qwen3.8-Vision-200B'); // HF order, no pin for a partial query
    expect(component['visibleModels']().some((m) => m.min_nodes === 2)).toBe(true);

    component['showMore']();
    fixture.detectChanges();
    expect(component['visibleModels']().length).toBe(12);
  });

  it('keeps curated metadata over a live duplicate (#168)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [{ ...exactModel, id: 'Qwen/Qwen2.5-32B-Instruct', est_memory_gb: 999 }],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('Qwen2.5-32B');
    await component['runSearch']();
    await fixture.whenStable();

    const row = component['filteredSearchModels']().find(
      (m) => m.id === 'Qwen/Qwen2.5-32B-Instruct',
    );
    expect(row?.est_memory_gb).toBe(65); // curated, not the live 999
  });

  it('matches an off-catalog active model by id, never by its stub label (#168)', async () => {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'someorg/Custom-13B-Instruct',
        enabled: true,
      }),
    );
    apiMock.searchAiModels.mockResolvedValue(response({ models: [] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('custom-13b');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(searchRowIds(fixture)).toEqual(['someorg/Custom-13B-Instruct']);

    // The generated "(not in catalog)" label alone must never match.
    component['onSearchInput']('not in catalog');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(searchRowIds(fixture)).toEqual([]);
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.search-results .page-empty'),
    ).not.toBeNull();
  });

  /** Configured and served ids point at the same off-catalog model (#168). */
  function configureDuplicateOffCatalog(servedModel: string): void {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm:8000',
        model: 'someorg/Custom-13B-Instruct',
        enabled: true,
      }),
    );
    apiMock.getAiRuntimeStatus.mockResolvedValue(
      response({
        enabled: true,
        provider: 'vllm',
        model: 'someorg/Custom-13B-Instruct',
        ready: true,
        served_model: servedModel,
        detail: 'ok',
        vision_ready: false,
        vision_model: null,
        vision_enabled: false,
      }),
    );
  }

  it('renders the configured-served off-catalog model once in search (#168)', async () => {
    configureDuplicateOffCatalog('someorg/Custom-13B-Instruct');
    apiMock.searchAiModels.mockResolvedValue(response({ models: [] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    // The regression: two stubs for one model rendered two identical rows,
    // counted twice, with duplicate @for track keys (NG0955).
    expect(component['activeStubs']().length).toBe(1);
    component['onSearchInput']('someorg/Custom-13B-Instruct');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(searchRowIds(fixture)).toEqual(['someorg/Custom-13B-Instruct']);
    expect(component['searchResultCount']()).toBe(1);
  });

  it('dedupes stubs case-insensitively when the served id differs only in case (#168)', async () => {
    configureDuplicateOffCatalog('SOMEORG/CUSTOM-13B-INSTRUCT');
    apiMock.searchAiModels.mockResolvedValue(response({ models: [] }));
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('someorg/custom-13b-instruct');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    // One stub, kept under the configured casing (first wins).
    expect(searchRowIds(fixture)).toEqual(['someorg/Custom-13B-Instruct']);
  });

  it('shows the off-catalog model once in the fallback after a failed search (#168)', async () => {
    configureDuplicateOffCatalog('someorg/Custom-13B-Instruct');
    apiMock.searchAiModels.mockResolvedValue(
      statusResponse(undefined, 503, {
        error: {
          code: 'http_error',
          message: 'Hugging Face is unreachable; showing curated models only',
        },
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['onSearchInput']('custom-13b');
    await component['runSearch']();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.search-results .page-error')?.textContent).toContain(
      'Could not search Hugging Face',
    );
    expect(searchRowIds(fixture)).toEqual(['someorg/Custom-13B-Instruct']);
  });
});
