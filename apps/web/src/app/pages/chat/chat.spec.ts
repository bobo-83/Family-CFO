import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Chat } from './chat';

function recommendation(answer: string, confidence = 0.85, answeredBy: string | null = 'Qwen/Qwen2.5-32B-Instruct') {
  return {
    id: 'rec-1',
    answer,
    assumptions: [],
    impacts: [],
    tradeoffs: [],
    alternatives: [],
    confidence,
    calculation_refs: ['financial_calculations:abc'],
    warnings: [],
    answered_by: answeredBy,
    photo_described_by: null as string | null,
  };
}

describe('Chat', () => {
  let apiMock: {
    getAiRuntimeStatus: ReturnType<typeof vi.fn>;
    listConversations: ReturnType<typeof vi.fn>;
    getConversation: ReturnType<typeof vi.fn>;
    createChatMessage: ReturnType<typeof vi.fn>;
    deleteConversation: ReturnType<typeof vi.fn>;
    synthesizeSpeech: ReturnType<typeof vi.fn>;
    submitAdvisorFeedback: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    apiMock = {
      getAiRuntimeStatus: vi.fn().mockResolvedValue({
        data: { enabled: true, provider: 'vllm', model: 'Qwen', ready: true, served_model: 'Qwen', detail: 'ok' },
      }),
      listConversations: vi.fn().mockResolvedValue({ data: { conversations: [] } }),
      getConversation: vi.fn(),
      createChatMessage: vi.fn(),
      deleteConversation: vi.fn(),
      synthesizeSpeech: vi.fn(),
      submitAdvisorFeedback: vi.fn().mockResolvedValue({ error: undefined }),
    };

    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    }).compileComponents();
  });

  it('does not send an empty message', async () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    await component['send']();
    expect(apiMock.createChatMessage).not.toHaveBeenCalled();
  });

  it('always shows the advisor disclaimer (ADR 0031)', async () => {
    const fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();
    await fixture.whenStable();
    const text = fixture.nativeElement.querySelector('.chat__disclaimer')?.textContent ?? '';
    expect(text).toContain('not financial, tax, or legal advice');
    expect(text).toContain('verify before acting');
  });

  it('sends the message with the current conversation id and appends both turns', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'conv-9', recommendation: recommendation('You can afford it.') },
    });
    const component = TestBed.createComponent(Chat).componentInstance;

    component['form'].setValue({ message: 'Can I afford a $1,000 phone?' });
    await component['send']();

    expect(apiMock.createChatMessage).toHaveBeenCalledWith({
      message: 'Can I afford a $1,000 phone?',
      conversation_id: undefined,
    });
    const turns = component['turns']();
    expect(turns[0]).toMatchObject({ role: 'user', content: 'Can I afford a $1,000 phone?' });
    expect(turns[1]).toMatchObject({ role: 'assistant', content: 'You can afford it.' });
    expect(component['conversationId']()).toBe('conv-9');

    // A follow-up carries the conversation id.
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'conv-9', recommendation: recommendation('Still yes.') },
    });
    component['form'].setValue({ message: 'And a case too?' });
    await component['send']();
    expect(apiMock.createChatMessage).toHaveBeenLastCalledWith({
      message: 'And a case too?',
      conversation_id: 'conv-9',
    });
  });

  it('reads an answer aloud, falling back to system speech when no voice service (M87a)', async () => {
    apiMock.synthesizeSpeech.mockResolvedValue(null); // 503 -> no on-box voice
    const speak = vi.fn();
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak },
      configurable: true,
    });
    // jsdom has neither symbol; the component uses both on the fallback path.
    (window as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance =
      class {
        onend: (() => void) | null = null;
        constructor(public text: string) {}
      };
    const component = TestBed.createComponent(Chat).componentInstance;

    await component['speak']('Your net worth is up.', 0);

    expect(apiMock.synthesizeSpeech).toHaveBeenCalledWith('Your net worth is up.');
    expect(speak).toHaveBeenCalledOnce();
  });

  it('surfaces an error and keeps the user turn', async () => {
    apiMock.createChatMessage.mockResolvedValue({ error: { error: { message: 'runtime down' } } });
    const component = TestBed.createComponent(Chat).componentInstance;

    component['form'].setValue({ message: 'hello' });
    await component['send']();

    expect(component['errorMessage']()).toBe('runtime down');
    expect(component['turns']()[0]).toMatchObject({ role: 'user', content: 'hello' });
  });

  it('startNewConversation clears the thread', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'c', recommendation: recommendation('hi') },
    });
    const component = TestBed.createComponent(Chat).componentInstance;
    component['form'].setValue({ message: 'hi' });
    await component['send']();
    expect(component['turns']().length).toBe(2);

    component['startNewConversation']();
    expect(component['turns']()).toEqual([]);
    expect(component['conversationId']()).toBeNull();
  });

  it('sends the attached image and clears it afterwards', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'c1', recommendation: recommendation('Looks affordable.') },
    });
    const component = TestBed.createComponent(Chat).componentInstance;

    component['attachedImage'].set({
      base64: 'aGVsbG8=',
      mediaType: 'image/jpeg',
      previewUrl: 'data:image/jpeg;base64,aGVsbG8=',
    });
    component['form'].setValue({ message: 'Can I afford this?' });
    await component['send']();

    expect(apiMock.createChatMessage).toHaveBeenCalledWith({
      message: 'Can I afford this?',
      conversation_id: undefined,
      image_base64: 'aGVsbG8=',
      image_media_type: 'image/jpeg',
    });
    expect(component['attachedImage']()).toBeNull();
    expect(component['turns']()[0]).toMatchObject({ role: 'user', hadImage: true });
  });

  it('removeImage clears the pending attachment without sending', () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    component['attachedImage'].set({
      base64: 'x',
      mediaType: 'image/jpeg',
      previewUrl: 'data:image/jpeg;base64,x',
    });

    component['removeImage']();

    expect(component['attachedImage']()).toBeNull();
    expect(apiMock.createChatMessage).not.toHaveBeenCalled();
  });

  it('renders model attribution on assistant turns', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'c', recommendation: recommendation('hi') },
    });
    const fixture = TestBed.createComponent(Chat);
    const component = fixture.componentInstance;
    component['form'].setValue({ message: 'hi' });
    await component['send']();
    fixture.detectChanges();

    const caption = (fixture.nativeElement as HTMLElement).querySelector('.chat__source');
    expect(caption?.textContent).toContain('Qwen/Qwen2.5-32B-Instruct');
  });

  it('shows both models when a photo was read', async () => {
    const rec = recommendation('Fits your budget.');
    rec.photo_described_by = 'Qwen/Qwen2.5-VL-7B-Instruct';
    apiMock.createChatMessage.mockResolvedValue({ data: { conversation_id: 'c', recommendation: rec } });
    const fixture = TestBed.createComponent(Chat);
    const component = fixture.componentInstance;
    component['form'].setValue({ message: 'how does this affect my savings?' });
    await component['send']();
    fixture.detectChanges();

    const caption = (fixture.nativeElement as HTMLElement).querySelector('.chat__source');
    expect(caption?.textContent).toContain('photo read by');
    expect(caption?.textContent).toContain('Qwen/Qwen2.5-VL-7B-Instruct');
  });

  it('marks deterministic answers as no-AI', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'c', recommendation: recommendation('snapshot', 0.85, null) },
    });
    const fixture = TestBed.createComponent(Chat);
    const component = fixture.componentInstance;
    component['form'].setValue({ message: 'hi' });
    await component['send']();
    fixture.detectChanges();

    const caption = (fixture.nativeElement as HTMLElement).querySelector('.chat__source');
    expect(caption?.textContent).toContain('Deterministic calculation');
  });

  it('deletes a conversation after confirmation and clears the open thread', async () => {
    apiMock.deleteConversation.mockResolvedValue({ data: undefined });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const component = TestBed.createComponent(Chat).componentInstance;
    component['conversationId'].set('conv-1');
    component['turns'].set([{ role: 'user', content: 'hi' }]);

    await component['deleteConversation']('conv-1', new Event('click'));

    expect(apiMock.deleteConversation).toHaveBeenCalledWith('conv-1');
    expect(component['conversationId']()).toBeNull();
    expect(component['turns']()).toEqual([]);
  });

  it('does not delete when the confirmation is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const component = TestBed.createComponent(Chat).componentInstance;

    await component['deleteConversation']('conv-1', new Event('click'));

    expect(apiMock.deleteConversation).not.toHaveBeenCalled();
  });

  it('exposes deletion only to owner/adult roles', () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    expect(component['canDeleteConversations']()).toBe(true);
  });

  it('maps confidence to a label', () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    expect(component['confidenceLabel'](0.85)).toBe('High');
    expect(component['confidenceLabel'](0.7)).toBe('Medium');
    expect(component['confidenceLabel'](0.4)).toBe('Low');
    expect(component['confidencePercent'](0.82)).toBe(82);
  });

  it('rates an answer, submitting feedback and marking the turn (ADR 0044)', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'conv-9', recommendation: recommendation('You can afford it.') },
    });
    const component = TestBed.createComponent(Chat).componentInstance;
    component['form'].setValue({ message: 'Can I?' });
    await component['send']();

    const turn = component['turns']().find((t: { role: string }) => t.role === 'assistant')!;
    await component['rate'](turn, 'down');

    expect(apiMock.submitAdvisorFeedback).toHaveBeenCalledWith('rec-1', 'down');
    expect(turn.rating).toBe('down');
  });

  it('reverts a failed rating and surfaces the error', async () => {
    apiMock.createChatMessage.mockResolvedValue({
      data: { conversation_id: 'conv-9', recommendation: recommendation('Yes.') },
    });
    apiMock.submitAdvisorFeedback.mockResolvedValue({ error: { detail: 'nope' } });
    const component = TestBed.createComponent(Chat).componentInstance;
    component['form'].setValue({ message: 'Can I?' });
    await component['send']();

    const turn = component['turns']().find((t: { role: string }) => t.role === 'assistant')!;
    await component['rate'](turn, 'up');

    expect(turn.rating).toBeUndefined(); // reverted
    expect(component['errorMessage']()).toBeTruthy();
  });
});
