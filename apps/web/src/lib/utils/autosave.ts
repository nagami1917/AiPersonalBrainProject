/**
 * Autosave for the note editor.
 *
 * Three problems this has to solve, in order of how badly they bite:
 *
 * 1. **Save storms.** Every content edit enqueues an ingest job server-side
 *    (the searchable representation has to be rebuilt). A naive "save on every
 *    keystroke, debounced" still writes once per pause, and a writer who pauses
 *    every few words would queue a reindex each time. So the debounce is long
 *    enough to coalesce typing, and an in-flight save *always* absorbs the next
 *    change rather than starting a second request.
 *
 * 2. **Lost edits.** If a save is in flight and the user keeps typing, the
 *    response that arrives describes the *old* text. Writing it into the field
 *    would revert their keystrokes. The saved text is compared against the
 *    current text, and only a still-matching field is marked clean.
 *
 * 3. **Leaving mid-flight.** Navigating away with an unsaved change silently
 *    loses it. `flush()` is exposed so the page can await it before navigating.
 */

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';

export type AutosaverOptions = {
  /** Debounce window. Long enough that typing does not enqueue a job per word. */
  delayMs?: number;
  save: (text: string) => Promise<void>;
  onState?: (state: SaveState, error?: string) => void;
};

export class Autosaver {
  #timer: ReturnType<typeof setTimeout> | null = null;
  /** The newest text the debounce is holding, or null when nothing is pending. */
  #queued: string | null = null;
  #inFlight: Promise<void> | null = null;
  /** Set while a save is running, to re-run once it finishes. */
  #pending: string | null = null;
  /** The text last confirmed written, used to decide whether a response is stale. */
  #saved = '';
  #delayMs: number;
  #save: (text: string) => Promise<void>;
  #onState: (state: SaveState, error?: string) => void;
  #disposed = false;

  constructor({ delayMs = 1200, save, onState }: AutosaverOptions) {
    this.#delayMs = delayMs;
    this.#save = save;
    this.#onState = onState ?? (() => {});
  }

  /** Seed with the text currently on the server, so the first keystroke is a change. */
  reset(text: string): void {
    this.#saved = text;
    this.#pending = null;
    this.#queued = null;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    this.#onState('idle');
  }

  /** Call on every keystroke. */
  change(text: string): void {
    if (this.#disposed) return;
    if (text === this.#saved) {
      // Typed back to the saved value: nothing to write, and no job to queue.
      if (this.#timer) clearTimeout(this.#timer);
      this.#timer = null;
      this.#queued = null;
      if (!this.#inFlight) this.#onState('idle');
      return;
    }

    this.#onState('dirty');
    this.#queued = text;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = setTimeout(() => {
      this.#timer = null;
      const next = this.#queued;
      this.#queued = null;
      if (next !== null) void this.#run(next);
    }, this.#delayMs);
  }

  /** Write anything queued, then await it. Call before navigating away.
   *
   * Cancelling the pending timer without writing would discard the edit — the
   * exact failure this method exists to prevent — so the queued text is
   * promoted to an immediate save instead.
   */
  async flush(): Promise<void> {
    if (this.#timer) {
      clearTimeout(this.#timer);
      this.#timer = null;
    }
    const queued = this.#queued;
    this.#queued = null;
    if (queued !== null && queued !== this.#saved) await this.#run(queued);
    if (this.#inFlight) await this.#inFlight;
  }

  /** Save immediately, ignoring the debounce. Used by the explicit Save button. */
  async saveNow(text: string): Promise<void> {
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    this.#queued = null;
    await this.#run(text);
  }

  dispose(): void {
    this.#disposed = true;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
  }

  async #run(text: string): Promise<void> {
    if (this.#disposed) return;

    // A save is already running: remember the newest text and let that save
    // finish first. Two concurrent writes to the same note would race, and the
    // loser's reindex job would be built from stale content.
    if (this.#inFlight) {
      this.#pending = text;
      return this.#inFlight;
    }

    this.#onState('saving');
    this.#inFlight = (async () => {
      try {
        await this.#save(text);
        this.#saved = text;
        // Only claim "saved" if the field has not moved on while we were writing.
        if (this.#pending === null) this.#onState('saved');
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Could not save.';
        this.#onState('error', message);
      } finally {
        this.#inFlight = null;
      }
    })();

    await this.#inFlight;

    // Whatever the outcome, if the text moved on, write again.
    const next = this.#pending;
    this.#pending = null;
    if (next !== null && next !== this.#saved) await this.#run(next);
  }
}

/** Human-readable autosave state. The editor shows this, so it must be honest. */
export const SAVE_LABEL: Record<SaveState, string> = {
  idle: 'Saved',
  dirty: 'Unsaved changes',
  saving: 'Saving…',
  saved: 'Saved',
  error: 'Not saved'
};
