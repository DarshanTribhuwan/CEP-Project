// Playback queue with barge-in support.
//
// This is the piece the reference implementation got right and it is kept as-is
// in spirit: audio is queued sentence by sentence and played back to back, and
// interruption is expressed as a session number. Bumping the session invalidates
// every in-flight synthesis and playback without needing to hunt down each
// promise, which is the part that gets racy when done with flags alone.
//
// The one addition over the reference version: synthesis is cancellable via
// AbortController, so a barge-in stops network work, not just playback.

import { bhashiniSpeak, browserSpeak, browserStop } from './tts.js';

export class Speaker {
  constructor(getConfig, lang = 'mr') {
    this.getConfig = getConfig;
    this.lang = lang;

    this.queue = [];
    this.current = null;
    this.isPlaying = false;
    this.session = 0;

    this.currentController = null;
    this.pumpPromise = null;
  }

  // Speak a sentence. Returns when this sentence has been *accepted* for
  // playback, not when it finishes - the queue drains on its own.
  async speak(text) {
    const sentence = (text || '').trim();
    if (!sentence) return;

    const session = this.session;
    const config = this.getConfig();
    const controller = new AbortController();
    this.currentController = controller;

    let prepared;
    try {
      prepared = await this.prepare(sentence, config, controller.signal);
    } catch (error) {
      if (session === this.session && error.name !== 'AbortError') {
        console.warn('synthesis failed:', error.message);
      }
      return;
    } finally {
      if (this.currentController === controller) this.currentController = null;
    }

    // A barge-in during synthesis means this audio is already unwanted.
    if (session !== this.session) {
      prepared?.dispose?.();
      return;
    }

    this.queue.push({ prepared, session });
    this.pump();
  }

  async prepare(sentence, config, signal) {
    if (config.bhashiniKey) {
      const blob = await bhashiniSpeak(sentence, config, signal);
      const url = URL.createObjectURL(blob);
      return {
        kind: 'url',
        url,
        dispose: () => URL.revokeObjectURL(url),
      };
    }

    return { kind: 'browser', sentence };
  }

  // The drain loop. One runner at a time; new items are picked up by the
  // running loop rather than starting a competing one.
  pump() {
    if (this.pumpPromise) return;
    this.pumpPromise = this.drain().finally(() => {
      this.pumpPromise = null;
      if (this.queue.length) this.pump();
    });
  }

  async drain() {
    this.isPlaying = true;
    this.onStateChange?.();

    try {
      while (this.queue.length) {
        const item = this.queue.shift();
        if (item.session !== this.session) {
          item.prepared.dispose?.();
          continue;
        }

        this.current = item;
        this.onItemStart?.(item);
        try {
          await this.play(item.prepared);
        } catch (error) {
          console.warn('playback failed:', error.message);
        } finally {
          item.prepared.dispose?.();
          this.current = null;
        }
      }
    } finally {
      this.isPlaying = false;
      this.onStateChange?.();
    }
  }

  play(prepared) {
    if (prepared.kind === 'browser') {
      return browserSpeak(prepared.sentence, this.lang);
    }

    return new Promise((resolve, reject) => {
      const audio = new Audio(prepared.url);
      this.currentController = null;

      const done = () => {
        audio.onended = null;
        audio.onerror = null;
        resolve();
      };

      audio.onended = done;
      audio.onerror = () => reject(audio.error ?? new Error('audio playback failed'));
      audio.play().catch(reject);
    });
  }

  // Stop everything: bump the session, silence the current utterance, drop the
  // queue, abort in-flight synthesis.
  interrupt() {
    this.session += 1;

    this.currentController?.abort();
    this.currentController = null;

    if (this.current?.prepared?.kind === 'browser') browserStop();

    const dropped = this.queue.splice(0);
    for (const item of dropped) item.prepared.dispose?.();
    this.current = null;
    this.isPlaying = false;
    this.onStateChange?.();
  }

  isSpeaking() {
    return Boolean(this.current) || this.isPlaying || this.queue.length > 0;
  }

  stopAll() {
    this.interrupt();
    this.session = 0;
  }
}
