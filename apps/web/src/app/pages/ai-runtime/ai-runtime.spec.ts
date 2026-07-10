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
      searchAiModels: vi.fn(),
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

  it('initializes the selection from the saved config and status', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    expect(component['selectedMainId']()).toBe('Qwen/Qwen2.5-32B-Instruct');
    expect(component['selectedVisionId']()).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
  });

  it('computes replacement requirements and fit against the memory budget', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    // 32B main (65) + VL-7B vision (16) = 81 * 1.15 ≈ 93 GB vs 120 GB budget.
    expect(component['requiredMemoryGb']()).toBe(93);
    expect(component['memoryVerdict']()).toBe('fits');
    expect(component['usingSystemMemoryBudget']()).toBe(true);

    // A vision-capable main REPLACES both models: vision drops out of the total.
    component['selectMain']('Qwen/Qwen2.5-VL-32B-Instruct');
    expect(component['selectedVision']()).toBeNull();
    expect(component['requiredMemoryGb']()).toBe(Math.round(70 * 1.15));
    expect(component['swapCommand']()).toBe('scripts/swap-model.sh Qwen/Qwen2.5-VL-32B-Instruct');
  });

  it('flags a selection that will not fit', async () => {
    apiMock.getAiHardwareProfile.mockResolvedValue(
      response({ gpu_memory_gb: 24, system_memory_gb: 64, disk_free_gb: 30, source: 'env' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    expect(component['memoryVerdict']()).toBe('no'); // 93 GB required vs 24 GB GPU
    expect(component['diskVerdict']()).toBe('no'); // 78 GB weights vs 30 GB free
  });

  it('saves the selection and builds the swap command with the vision model', async () => {
    apiMock.updateAiRuntimeConfig.mockResolvedValue(response({}));
    const fixture = await create();
    const component = fixture.componentInstance;

    expect(component['swapCommand']()).toBe(
      'scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct Qwen/Qwen2.5-VL-7B-Instruct',
    );
    await component['saveSelection']();
    expect(apiMock.updateAiRuntimeConfig).toHaveBeenCalledWith({
      provider: 'vllm',
      base_url: 'http://vllm:8000',
      model: 'Qwen/Qwen2.5-32B-Instruct',
      enabled: true,
    });
  });

  it('reports a serving mismatch when the selection differs from the served model', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    expect(component['servingMismatch']()).toBe(false);

    component['selectMain']('Qwen/Qwen2.5-VL-32B-Instruct');
    expect(component['servingMismatch']()).toBe(true);
  });

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

  it('merges HF search results into the picker (curated wins on duplicates)', async () => {
    apiMock.searchAiModels.mockResolvedValue(
      response({
        models: [
          {
            id: 'mistralai/Mistral-7B-Instruct-v0.3',
            label: 'Mistral-7B-Instruct-v0.3 (Hugging Face)',
            role: 'main',
            parameters_b: 7,
            est_memory_gb: 15,
            est_disk_gb: 14,
            tool_parser: 'hermes',
            supports_vision: false,
            gated: false,
            notes: 'Estimated specs',
          },
          // Duplicate of a curated model: must not double up.
          CATALOG[0],
        ],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    component['searchQuery'].set('mistral');
    await component['runSearch']();

    const ids = component['mainOptions']().map((m) => m.id);
    expect(ids).toContain('mistralai/Mistral-7B-Instruct-v0.3');
    expect(ids.filter((id) => id === 'Qwen/Qwen2.5-32B-Instruct').length).toBe(1);
  });

  it('apply calls the endpoint and reaches active when the served model matches', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    await component['apply']();
    expect(apiMock.applyAiModelSelection).toHaveBeenCalledWith({
      main_model: 'Qwen/Qwen2.5-32B-Instruct',
      vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
    });
    expect(component['applyLive']()?.phase).toBe('working');

    // Manager finishes; the served model already matches the selection.
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
    await component['apply']();

    apiMock.getAiApplyStatus.mockResolvedValue(
      response({ state: 'failed', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: 'boom' }),
    );
    await component['pollOnce']();
    expect(component['applyLive']()?.phase).toBe('failed');
    expect(component['applyLive']()?.detail).toBe('boom');
  });

  // --- M47: redesign behaviors ------------------------------------------------

  it('keeps an off-catalog active model visible via a synthesized stub', async () => {
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

    // The fix: previously this selection resolved to null and the whole
    // fit/apply section disappeared after a reload.
    const main = component['selectedMain']();
    expect(main).not.toBeNull();
    expect(main!.id).toBe('someorg/Custom-13B-Instruct');
    expect(main!.label).toContain('not in catalog');
    expect(main!.parameters_b).toBe(13); // estimated from the name
    expect(component['requiredMemoryGb']()).not.toBeNull();
  });

  it('recommended quick filter keeps only main models that fit, strongest first', async () => {
    // 120 GB budget: 32B (65) and VL-32B (70) fit; add an oversized entry via search.
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
        ],
      }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;
    component['searchQuery'].set('huge');
    await component['runSearch']();

    component['setQuickFilter']('recommended');
    const ids = component['filteredModels']().map((m) => m.id);
    expect(ids).not.toContain('big/Huge-200B-Instruct'); // does not fit
    expect(ids[0]).toBe('Qwen/Qwen2.5-VL-32B-Instruct'); // strongest fitting first (33B)
  });

  it('vision quick filters sort vision-capable models by size', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    component['setQuickFilter']('vision-big');
    let models = component['filteredModels']();
    expect(models.every((m) => m.supports_vision)).toBe(true);
    expect(models[0].id).toBe('Qwen/Qwen2.5-VL-32B-Instruct');

    component['setQuickFilter']('vision-small');
    models = component['filteredModels']();
    expect(models[0].id).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
  });

  it('finance quick filter keeps only tool-calling-capable main models', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    component['setQuickFilter']('finance');
    const models = component['filteredModels']();
    expect(models.length).toBeGreaterThan(0);
    expect(models.every((m) => Boolean(m.tool_parser))).toBe(true);
    expect(models.every((m) => m.role === 'main' || m.role === 'both')).toBe(true);
  });

  it('selectFor routes vision-role models to the vision slot', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    component['selectFor'](CATALOG[2] as never); // role: vision
    expect(component['selectedVisionId']()).toBe('Qwen/Qwen2.5-VL-7B-Instruct');
    component['selectFor'](CATALOG[0] as never); // role: main
    expect(component['selectedMainId']()).toBe('Qwen/Qwen2.5-32B-Instruct');
  });

  it('applies a vision model change from the Advanced section via the swap', async () => {
    apiMock.applyAiModelSelection.mockResolvedValue(
      response({ state: 'running', main_model: 'Qwen/Qwen2.5-32B-Instruct', log_tail: '' }),
    );
    const fixture = await create();
    const component = fixture.componentInstance;

    // Prefilled from the live status.
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
