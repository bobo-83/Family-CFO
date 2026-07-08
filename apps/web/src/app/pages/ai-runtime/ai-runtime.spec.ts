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

describe('AiRuntime', () => {
  let apiMock: {
    getAiRuntimeConfig: ReturnType<typeof vi.fn>;
    updateAiRuntimeConfig: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    apiMock = { getAiRuntimeConfig: vi.fn(), updateAiRuntimeConfig: vi.fn() };
  });

  it('shows the form pre-filled for an owner and saves changes', async () => {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({ provider: 'vllm', base_url: 'http://vllm:8000', model: '', enabled: false }),
    );
    apiMock.updateAiRuntimeConfig.mockResolvedValue(
      response({
        provider: 'vllm',
        base_url: 'http://vllm.local:8000',
        model: 'llama-3-8b-instruct',
        enabled: true,
      }),
    );

    TestBed.configureTestingModule({
      imports: [AiRuntime],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(AiRuntime);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component['form'].value.base_url).toBe('http://vllm:8000');

    component['form'].patchValue({
      base_url: 'http://vllm.local:8000',
      model: 'llama-3-8b-instruct',
      enabled: true,
    });
    await component['submit']();

    expect(apiMock.updateAiRuntimeConfig).toHaveBeenCalledWith({
      provider: 'vllm',
      base_url: 'http://vllm.local:8000',
      model: 'llama-3-8b-instruct',
      enabled: true,
    });
  });

  it('hides the form for a non-owner', async () => {
    apiMock.getAiRuntimeConfig.mockResolvedValue(
      response({ provider: 'vllm', base_url: 'http://vllm:8000', model: '', enabled: false }),
    );

    TestBed.configureTestingModule({
      imports: [AiRuntime],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'adult' } },
      ],
    });
    const fixture = TestBed.createComponent(AiRuntime);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const form = (fixture.nativeElement as HTMLElement).querySelector('.ai-runtime-form');
    expect(form).toBeFalsy();
  });
});
