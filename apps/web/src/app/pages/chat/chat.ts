import { Component, HostListener, OnDestroy, inject, resource, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import type { Recommendation } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { speakableSentences } from '../../shared/speakable-text';

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  recommendation?: Recommendation;
  recommendationId?: string; // set on history turns, where the full object isn't loaded
  rating?: 'up' | 'down'; // ADR 0044: this member's rating, once given
  showNote?: boolean; // a 👎 opened the optional note box
  noteDraft?: string;
  hadImage?: boolean;
}

interface AttachedImage {
  base64: string;
  mediaType: 'image/jpeg' | 'application/pdf';
  previewUrl: string | null; // null for PDFs (no thumbnail; the name shows)
  name?: string;
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

/** M84a: PDFs go up as-is — the server rasterizes page 1 for the vision model. */
export function encodePdfFile(file: File): Promise<AttachedImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        base64: String(reader.result).split(',')[1],
        mediaType: 'application/pdf',
        previewUrl: null,
        name: file.name,
      });
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

const EXAMPLE_PROMPTS = [
  'Can I afford a $1,000 phone?',
  'How are we doing this month?',
  'If I invest $5,000 at 6% for 20 years, what could it grow to?',
];

@Component({
  selector: 'app-chat',
  imports: [
    ReactiveFormsModule,
    FormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MarkdownPipe,
  ],
  templateUrl: './chat.html',
  styleUrl: './chat.scss',
})
export class Chat implements OnDestroy {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  /** Conversation deletion mirrors the API's role gate (owner/adult). */
  protected readonly canDeleteConversations = () => {
    return this.auth.hasRight('advisor.manage');
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
    await this.attachFile(file);
  }

  /** M118 (ADR 0028): Ctrl/⌘+V attaches a copied image or PDF to the composer. */
  @HostListener('window:paste', ['$event'])
  async onPaste(event: ClipboardEvent): Promise<void> {
    const items = event.clipboardData?.items ?? [];
    for (const item of Array.from(items)) {
      if (item.kind !== 'file') {
        continue;
      }
      const file = item.getAsFile();
      if (file && /^(image\/|application\/pdf)/.test(file.type)) {
        event.preventDefault();
        await this.attachFile(file);
        return;
      }
    }
  }

  protected async attachFile(file: File | undefined | null): Promise<void> {
    if (!file) {
      return;
    }
    try {
      this.attachedImage.set(
        file.type === 'application/pdf'
          ? await encodePdfFile(file)
          : await encodeImageFile(file),
      );
      this.errorMessage.set(null);
    } catch {
      this.errorMessage.set('Could not read that file — try a different photo or PDF.');
    }
  }

  protected removeImage(): void {
    this.attachedImage.set(null);
  }

  // M85: a data file (CSV / spreadsheet / text) — summarized server-side.
  protected readonly attachedFile = signal<{ base64: string; name: string } | null>(null);

  protected async onDataFileSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) {
      return;
    }
    try {
      const base64: string = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      this.attachedFile.set({ base64, name: file.name });
      this.errorMessage.set(null);
    } catch {
      this.errorMessage.set('Could not read that file — try a CSV, spreadsheet, or text file.');
    }
  }

  protected removeFile(): void {
    this.attachedFile.set(null);
  }

  // M87a: read an assistant answer aloud via the on-box voice service, falling
  // back to the browser's built-in speech synthesizer when it isn't available.
  protected readonly speaking = signal<number | null>(null);
  // ADR 0052 addendum: playback goes through an HTML <audio> ELEMENT, not Web
  // Audio — on iOS the ring/silent switch MUTES Web Audio (and speechSynthesis)
  // but media elements play regardless, matching the native app. The element is
  // "unlocked" by playing a silent clip inside the tap gesture so the src can
  // be swapped to the fetched speech after the await. Web Audio remains a
  // fallback for browsers where element playback fails.
  private ttsAudio: HTMLAudioElement | null = null;
  private audioContext: AudioContext | null = null;
  private currentSource: AudioBufferSourceNode | null = null;
  // 1 sample of 16-bit mono silence — a valid, instantly-ending WAV.
  private static readonly SILENCE =
    'data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAgLsAAAB3AQACABAAZGF0YQIAAAAAAA==';

  // Stale-async guard: every start/stop bumps the run id, so a destroyed or
  // superseded pipeline can never start audio (the duplicate-readings bug).
  private speakRun = 0;

  ngOnDestroy(): void {
    // Leaving the conversation must stop the voice — the component dies but a
    // playing <audio> element would not.
    this.stopSpeaking();
  }

  protected async speak(text: string, index: number): Promise<void> {
    // Tapping the one that's playing (or any, mid-playback) stops it.
    if (this.speaking() !== null) {
      const wasThis = this.speaking() === index;
      this.stopSpeaking();
      if (wasThis) {
        return;
      }
    }
    this.speaking.set(index);
    const run = ++this.speakRun;

    // Sentence pipeline (like the iOS voice): synthesize sentence i+1 while
    // sentence i plays, so first sound arrives after ONE sentence.
    const sentences = speakableSentences(text);
    if (sentences.length === 0) {
      this.speaking.set(null);
      return;
    }

    // Unlock the media element within the user gesture.
    const audio = this.ttsAudio ?? new Audio();
    this.ttsAudio = audio;
    let elementUnlocked = false;
    try {
      audio.src = Chat.SILENCE;
      await audio.play();
      elementUnlocked = true;
    } catch {
      // Blocked (rare once unlocked before) — the Web Audio fallback below.
    }

    const synthesize = async (sentence: string): Promise<ArrayBuffer | null> => {
      try {
        return await this.api.synthesizeSpeechBuffer(sentence);
      } catch {
        return null;
      }
    };

    let next = synthesize(sentences[0]);
    for (let i = 0; i < sentences.length; i += 1) {
      const buffer = await next;
      if (run !== this.speakRun) {
        return; // stopped or superseded while synthesizing
      }
      if (i + 1 < sentences.length) {
        next = synthesize(sentences[i + 1]);
      }
      if (!buffer || buffer.byteLength === 0) {
        // No on-box voice (503) or a failed chunk: speak the REST with the
        // platform synthesizer rather than skipping content silently.
        this.fallbackSpeak(sentences.slice(i).join(' '), index);
        return;
      }
      const played =
        (elementUnlocked && (await this.playChunkOnElement(audio, buffer, run))) ||
        (await this.playChunkViaWebAudio(buffer, run));
      if (run !== this.speakRun) {
        return;
      }
      if (!played) {
        this.fallbackSpeak(sentences.slice(i).join(' '), index);
        return;
      }
    }
    if (run === this.speakRun && this.speaking() === index) {
      this.speaking.set(null);
    }
  }

  /** Play one WAV chunk on the unlocked element; resolves when it ends. */
  private playChunkOnElement(
    audio: HTMLAudioElement,
    buffer: ArrayBuffer,
    run: number,
  ): Promise<boolean> {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }));
      const done = (ok: boolean) => {
        URL.revokeObjectURL(url);
        audio.onended = null;
        audio.onerror = null;
        resolve(ok && run === this.speakRun);
      };
      audio.onended = () => done(true);
      audio.onerror = () => done(false);
      audio.src = url;
      audio.play().catch(() => done(false));
    });
  }

  /** Fallback for browsers where element playback fails; resolves on end. */
  private async playChunkViaWebAudio(buffer: ArrayBuffer, run: number): Promise<boolean> {
    const context = this.ensureAudioContext();
    if (!context) {
      return false;
    }
    if (context.state === 'suspended') {
      try {
        await context.resume();
      } catch {
        return false;
      }
    }
    try {
      const decoded = await context.decodeAudioData(buffer.slice(0));
      if (run !== this.speakRun) {
        return true; // stopped; report handled so no fallback voice kicks in
      }
      return await new Promise((resolve) => {
        const source = context.createBufferSource();
        source.buffer = decoded;
        source.connect(context.destination);
        source.onended = () => {
          if (this.currentSource === source) {
            this.currentSource = null;
          }
          resolve(true);
        };
        this.currentSource = source;
        source.start();
      });
    } catch {
      return false;
    }
  }

  private ensureAudioContext(): AudioContext | null {
    if (this.audioContext) {
      return this.audioContext;
    }
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) {
      return null;
    }
    this.audioContext = new Ctor();
    return this.audioContext;
  }

  private fallbackSpeak(text: string, index: number): void {
    if (this.speaking() !== index) {
      return;
    }
    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.onend = () => {
        if (this.speaking() === index) {
          this.speaking.set(null);
        }
      };
      utterance.onerror = () => {
        if (this.speaking() === index) {
          this.speaking.set(null);
        }
      };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } else {
      this.speaking.set(null);
    }
  }

  protected stopSpeaking(): void {
    this.speakRun += 1; // invalidate any in-flight pipeline
    if (this.ttsAudio) {
      this.ttsAudio.pause();
      this.ttsAudio.onended = null;
      this.ttsAudio.onerror = null;
    }
    if (this.currentSource) {
      try {
        this.currentSource.stop();
      } catch {
        // Already stopped.
      }
      this.currentSource = null;
    }
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    this.speaking.set(null);
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
    this.turns.set(
      data.messages.map((m) => ({
        role: m.role,
        content: m.content,
        recommendationId: m.recommendation_id ?? undefined,
      })),
    );
  }

  // ADR 0044: rate an advisor answer; the idle study job learns from it. The
  // rating shows immediately (optimistic) and is safe to change.
  protected async rate(turn: ChatTurn, rating: 'up' | 'down', note?: string): Promise<void> {
    const recommendationId = turn.recommendation?.id ?? turn.recommendationId;
    if (!recommendationId) return;
    const previous = turn.rating;
    turn.rating = rating;
    // A 👎 opens the note box; a 👍 closes it.
    turn.showNote = rating === 'down';
    this.turns.update((turns) => [...turns]);
    const trimmed = note?.trim();
    const { error } = await this.api.submitAdvisorFeedback(
      recommendationId,
      rating,
      trimmed ? trimmed : undefined,
    );
    if (error) {
      turn.rating = previous;
      this.turns.update((turns) => [...turns]);
      this.errorMessage.set(apiErrorMessage(error, 'Could not save your feedback.'));
    }
  }

  // Send the typed note; it updates the same feedback row (upsert).
  protected async sendNote(turn: ChatTurn): Promise<void> {
    await this.rate(turn, 'down', turn.noteDraft);
    turn.showNote = false;
    this.turns.update((turns) => [...turns]);
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
    const file = this.attachedFile();
    this.sending.set(true);
    this.errorMessage.set(null);
    this.turns.update((turns) => [
      ...turns,
      { role: 'user', content: message, hadImage: image !== null },
    ]);
    this.form.reset({ message: '' });
    this.attachedImage.set(null);
    this.attachedFile.set(null);

    const { data, error } = await this.api.createChatMessage({
      message,
      conversation_id: this.conversationId() ?? undefined,
      image_base64: image?.base64,
      image_media_type: image?.mediaType,
      data_file_base64: file?.base64,
      data_file_name: file?.name,
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
