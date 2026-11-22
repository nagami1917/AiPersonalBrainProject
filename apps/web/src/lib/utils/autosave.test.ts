import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Autosaver, type SaveState } from './autosave';

/**
 * Autosave is the one piece of this UI where a bug silently destroys work, so
 * the tests are about ordering and failure, not about "does it call the API".
 */

describe('Autosaver', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function makeSaver(save: (text: string) => Promise<void>, states: SaveState[] = []) {
    return new Autosaver({
      delayMs: 1000,
      save,
      onState: (state) => states.push(state)
    });
  }

  it('does not save before the debounce elapses', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('');

    saver.change('a');
    await vi.advanceTimersByTimeAsync(999);
    expect(save).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledWith('a');
  });

  it('coalesces a burst of keystrokes into one write', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('');

    saver.change('a');
    await vi.advanceTimersByTimeAsync(400);
    saver.change('ab');
    await vi.advanceTimersByTimeAsync(400);
    saver.change('abc');
    await vi.advanceTimersByTimeAsync(1000);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith('abc');
  });

  it('does not save when the text returns to the saved value', async () => {
    // Typing a character and deleting it must not enqueue a reindex job.
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('original');

    saver.change('original!');
    saver.change('original');
    await vi.advanceTimersByTimeAsync(5000);

    expect(save).not.toHaveBeenCalled();
  });

  it('never runs two saves concurrently', async () => {
    let inFlight = 0;
    let maxConcurrent = 0;
    const save = vi.fn(async () => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 500));
      inFlight -= 1;
    });

    const saver = makeSaver(save);
    saver.reset('');
    saver.change('one');
    await vi.advanceTimersByTimeAsync(1000); // starts save("one")

    // A change during the in-flight save must queue, not race.
    saver.change('two');
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(1000);

    expect(maxConcurrent).toBe(1);
    expect(save).toHaveBeenCalledWith('one');
    expect(save).toHaveBeenCalledWith('two');
  });

  it('writes the newest text even if several changes arrive mid-save', async () => {
    const saved: string[] = [];
    const save = vi.fn(async (text: string) => {
      saved.push(text);
      await new Promise((resolve) => setTimeout(resolve, 300));
    });

    const saver = makeSaver(save);
    saver.reset('');
    saver.change('first');
    await vi.advanceTimersByTimeAsync(1000); // in-flight: "first"

    saver.change('second');
    saver.change('third');
    await vi.advanceTimersByTimeAsync(300 + 1000);

    // The queued write must be the LATEST text, not the intermediate one.
    expect(saved[saved.length - 1]).toBe('third');
  });

  it('reports an error rather than pretending it saved', async () => {
    const states: SaveState[] = [];
    const save = vi.fn().mockRejectedValue(new Error('offline'));
    const saver = makeSaver(save, states);
    saver.reset('');

    saver.change('text');
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    expect(states).toContain('saving');
    expect(states).toContain('error');
    expect(states).not.toContain('saved');
  });

  it('retries after a failure when the user edits again', async () => {
    let attempt = 0;
    const save = vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error('first attempt fails');
    });
    const saver = makeSaver(save);
    saver.reset('');

    saver.change('draft');
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    saver.change('draft 2');
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    expect(save).toHaveBeenCalledTimes(2);
  });

  it('saveNow bypasses the debounce', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('');

    await saver.saveNow('immediate');
    expect(save).toHaveBeenCalledWith('immediate');
  });

  it('flush writes a pending debounce instead of discarding it', async () => {
    // The bug this pins down: cancelling the timer without saving loses the edit
    // precisely when the user is navigating away.
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('');

    saver.change('unsaved words');
    const flushing = saver.flush();
    await vi.advanceTimersByTimeAsync(0);
    await flushing;

    expect(save).toHaveBeenCalledWith('unsaved words');
  });

  it('flush is a no-op when nothing is pending', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('unchanged');

    await saver.flush();
    expect(save).not.toHaveBeenCalled();
  });

  it('flush waits for an in-flight write', async () => {
    let finished = false;
    const save = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
      finished = true;
    });
    const saver = makeSaver(save);
    saver.reset('');

    saver.change('text');
    await vi.advanceTimersByTimeAsync(1000);

    const flushing = saver.flush();
    await vi.advanceTimersByTimeAsync(200);
    await flushing;
    expect(finished).toBe(true);
  });

  it('stops saving after dispose', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const saver = makeSaver(save);
    saver.reset('');
    saver.change('text');
    saver.dispose();

    await vi.advanceTimersByTimeAsync(5000);
    expect(save).not.toHaveBeenCalled();
  });
});
