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

  it('hides the configuration for a non-owner', async () => {
    const fixture = await create('adult');
    const form = (fixture.nativeElement as HTMLElement).querySelector('.ai-runtime-form');
    const picker = (fixture.nativeElement as HTMLElement).querySelector('.picker');
    expect(form).toBeFalsy();
    expect(picker).toBeFalsy();
  });
});
