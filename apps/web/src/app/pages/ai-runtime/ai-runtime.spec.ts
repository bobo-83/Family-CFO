import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { AiRuntime } from './ai-runtime';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

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
    tool_parser: 'hermes',
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
    applyAiModelSelection: ReturnType<typeof vi.fn>;
    getAiApplyStatus: ReturnType<typeof vi.fn>;
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
        response({ gpu_memory_gb: null, system_memory_gb: 120, disk_free_gb: 500, source: 'system' }),
      ),
      searchAiModels: vi.fn().mockResolvedValue(response({ models: [] })),
      applyAiModelSelection: vi.fn(),
      getAiApplyStatus: vi.fn().mockResolvedValue(response({ state: 'idle', log_tail: '' })),
    };
  });

  async function create(role = 'owner') {
    TestBed.configureTestingModule({
      imports: [AiRuntime],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => role } },
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

  it('applyModel posts the plan and reaches active when serving matches', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['applyModel'](CATALOG[0] as never);
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
    });
    expect(component['applyLive']()?.phase).toBe('working');

    apiMock.getAiApplyStatus.mockResolvedValue(
      response({ state: 'succeeded', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: 'ok' }),
    );
    await component['pollOnce']();
    await fixture.whenStable();
    expect(component['applyLive']()?.phase).toBe('active');
    component['stopPolling']();
  });

  it('surfaces a failed swap with its log tail', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    await component['applyModel'](CATALOG[0] as never);

    apiMock.getAiApplyStatus.mockResolvedValue(
      response({ state: 'failed', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: 'boom' }),
    );
    await component['pollOnce']();
    expect(component['applyLive']()?.phase).toBe('failed');
    expect(component['applyLive']()?.detail).toBe('boom');
    component['stopPolling']();
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
      limit: 20,
    });

    await component['setQuickFilter']('finance');
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      q: 'finance',
      pipeline: 'text-generation',
      limit: 20,
    });

    await component['setQuickFilter']('recommended');
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      pipeline: 'text-generation',
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
    expect(ids[0]).toBe('neat/Solid-40B-Instruct'); // strongest fitting, from the live list
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

  it('vision-big fans out size-hinted live queries and merges results', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    apiMock.searchAiModels.mockClear();
    apiMock.searchAiModels
      .mockResolvedValueOnce(
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
          ],
        }),
      )
      .mockResolvedValueOnce(
        response({
          models: [
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
      )
      .mockResolvedValueOnce(response({ models: [] }));

    await component['setQuickFilter']('vision-big');
    expect(apiMock.searchAiModels).toHaveBeenCalledTimes(3);
    expect(apiMock.searchAiModels).toHaveBeenCalledWith({
      q: '72B',
      pipeline: 'image-text-to-text',
      limit: 20,
    });
    // Both fan-out results merged; 72B AWQ (fits: 47*1.15 < 120) tops the list.
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
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['applyAsVision'](CATALOG[1] as never); // VL-32B, role both
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct', // current served main, unchanged
      vision_model: 'Qwen/Qwen2.5-VL-32B-Instruct',
    });
    component['stopPolling']();
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
    });
  });

  it('applies a vision model change from the Advanced section via the swap', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    expect(component['visionModelInput']()).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
    component['visionModelInput'].set('Qwen/Qwen2.5-VL-3B-Instruct');
    await component['applyVisionModel']();

    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-3B-Instruct',
    });
    component['stopPolling']();
  });

  it('hides the configuration for a non-owner', async () => {
    const fixture = await create('adult');
    const form = (fixture.nativeElement as HTMLElement).querySelector('.ai-runtime-form');
    const picker = (fixture.nativeElement as HTMLElement).querySelector('.picker');
    expect(form).toBeFalsy();
    expect(picker).toBeFalsy();
  });
});
