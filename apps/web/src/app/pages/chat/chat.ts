import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { Recommendation } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  recommendation?: Recommendation;
  hadImage?: boolean;
}

interface AttachedImage {
  base64: string;
  mediaType: 'image/jpeg';
  previewUrl: string;
}

/**
 * Downscale + re-encode a photo to a small JPEG data URL (max ~1280px). This
 * also normalizes iPhone HEIC into JPEG, since the canvas always encodes JPEG.
 */
export async function encodeImageFile(file: File, maxDimension = 1280): Promise<AttachedImage> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('canvas unavailable');
  }
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const previewUrl = canvas.toDataURL('image/jpeg', 0.85);
  return { base64: previewUrl.split(',')[1], mediaType: 'image/jpeg', previewUrl };
}

const EXAMPLE_PROMPTS = [
  'Can I afford a $1,000 phone?',
  'How are we doing this month?',
  'If I invest $5,000 at 6% for 20 years, what could it grow to?',
];

@Component({
  selector: 'app-chat',
  imports: [ReactiveFormsModule],
  templateUrl: './chat.html',
  styleUrl: './chat.scss',
})
export class Chat {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  /** Conversation deletion mirrors the API's role gate (owner/adult). */
  protected readonly canDeleteConversations = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly examplePrompts = EXAMPLE_PROMPTS;
  protected readonly formatMoney = formatMoney;

  // Live runtime status for the banner: is a model loaded, and which one.
  protected readonly aiStatus = resource({
    loader: async () => {
      const { data, error } = await this.api.getAiRuntimeStatus();
      if (error || !data) {
        throw new Error(apiErrorMessage(error, 'Could not check AI status.'));
      }
      return data;
    },
  });

  protected readonly turns = signal<ChatTurn[]>([]);
  protected readonly conversationId = signal<string | null>(null);
  protected readonly sending = signal(false);
  protected readonly errorMessage = signal<string | null>(null);
  protected readonly attachedImage = signal<AttachedImage | null>(null);

  protected readonly form = this.formBuilder.nonNullable.group({
    message: ['', Validators.required],
  });

  // Past conversations for the history sidebar (M10).
  protected readonly conversations = resource({
    loader: async () => {
      const { data, error } = await this.api.listConversations();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load conversations.'));
      }
      return data.conversations;
    },
  });

  protected usePrompt(prompt: string): void {
    this.form.setValue({ message: prompt });
  }

  protected async onFileSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = ''; // allow re-selecting the same file
    if (!file) {
      return;
    }
    try {
      this.attachedImage.set(await encodeImageFile(file));
      this.errorMessage.set(null);
    } catch {
      this.errorMessage.set('Could not read that image — try a different photo.');
    }
  }

  protected removeImage(): void {
    this.attachedImage.set(null);
  }

  /** A coarse High/Medium/Low label for a 0..1 confidence score. */
  protected confidenceLabel(confidence: number): 'High' | 'Medium' | 'Low' {
    if (confidence >= 0.8) {
      return 'High';
    }
    if (confidence >= 0.6) {
      return 'Medium';
    }
    return 'Low';
  }

  protected confidencePercent(confidence: number): number {
    return Math.round(confidence * 100);
  }

  protected startNewConversation(): void {
    this.turns.set([]);
    this.conversationId.set(null);
    this.errorMessage.set(null);
    this.attachedImage.set(null);
    this.form.reset({ message: '' });
  }

  protected async deleteConversation(id: string, event: Event): Promise<void> {
    event.stopPropagation();
    if (!window.confirm('Delete this conversation and its messages? This cannot be undone.')) {
      return;
    }
    const { error } = await this.api.deleteConversation(id);
    if (error) {
      this.errorMessage.set(apiErrorMessage(error, 'Failed to delete the conversation.'));
      return;
    }
    if (this.conversationId() === id) {
      this.startNewConversation();
    }
    this.conversations.reload();
  }

  protected formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  protected async openConversation(id: string): Promise<void> {
    this.errorMessage.set(null);
    const { data, error } = await this.api.getConversation(id);
    if (error || !data) {
      this.errorMessage.set(apiErrorMessage(error, 'Failed to open that conversation.'));
      return;
    }
    this.conversationId.set(data.id);
    this.turns.set(data.messages.map((m) => ({ role: m.role, content: m.content })));
  }

  protected async send(): Promise<void> {
    if (this.form.invalid || this.sending()) {
      this.form.markAllAsTouched();
      return;
    }

    const message = this.form.getRawValue().message.trim();
    if (!message) {
      return;
    }

    const image = this.attachedImage();
    this.sending.set(true);
    this.errorMessage.set(null);
    this.turns.update((turns) => [
      ...turns,
      { role: 'user', content: message, hadImage: image !== null },
    ]);
    this.form.reset({ message: '' });
    this.attachedImage.set(null);

    const { data, error } = await this.api.createChatMessage({
      message,
      conversation_id: this.conversationId() ?? undefined,
      image_base64: image?.base64,
      image_media_type: image?.mediaType,
    });

    this.sending.set(false);

    if (error || !data) {
      this.errorMessage.set(apiErrorMessage(error, 'The advisor could not answer. Please try again.'));
      return;
    }

    this.conversationId.set(data.conversation_id);
    this.turns.update((turns) => [
      ...turns,
      { role: 'assistant', content: data.recommendation.answer, recommendation: data.recommendation },
    ]);
    this.conversations.reload();
  }
}
