import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { Chat } from './chat';

function recommendation(answer: string, confidence = 0.85) {
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
  };
}

describe('Chat', () => {
  let apiMock: {
    getAiRuntimeStatus: ReturnType<typeof vi.fn>;
    listConversations: ReturnType<typeof vi.fn>;
    getConversation: ReturnType<typeof vi.fn>;
    createChatMessage: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    apiMock = {
      getAiRuntimeStatus: vi.fn().mockResolvedValue({
        data: { enabled: true, provider: 'vllm', model: 'Qwen', ready: true, served_model: 'Qwen', detail: 'ok' },
      }),
      listConversations: vi.fn().mockResolvedValue({ data: { conversations: [] } }),
      getConversation: vi.fn(),
      createChatMessage: vi.fn(),
    };

    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [{ provide: ApiService, useValue: apiMock }],
    }).compileComponents();
  });

  it('does not send an empty message', async () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    await component['send']();
    expect(apiMock.createChatMessage).not.toHaveBeenCalled();
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

  it('maps confidence to a label', () => {
    const component = TestBed.createComponent(Chat).componentInstance;
    expect(component['confidenceLabel'](0.85)).toBe('High');
    expect(component['confidenceLabel'](0.7)).toBe('Medium');
    expect(component['confidenceLabel'](0.4)).toBe('Low');
    expect(component['confidencePercent'](0.82)).toBe(82);
  });
});
