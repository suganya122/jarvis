/** When a spoken request is finished: after it heard words, 2 s of quiet audio (or 3 s with no new words). */

export const AUTO_SEND_SILENCE_MS = 2000;
/** The noisy-room fallback waits a little longer than the silence rule, so silence decides when it can. */
const STALLED_EXTRA_MS = 1000;
/** RMS of a float frame below this is quiet (the mic already has noise suppression on). */
const QUIET_RMS = 0.012;

export function rms(samples: Float32Array): number {
  let sum = 0;
  for (const s of samples) sum += s * s;
  return samples.length ? Math.sqrt(sum / samples.length) : 0;
}

/**
 * Two signals, either one ends the request: 2 s of quiet audio (the normal case), or 3 s with
 * no new words, for a noisy room (a fan, a TV) where the audio never goes quiet. Nothing is sent before
 * any words were heard, so opening the mic and thinking first never sends an empty request.
 */
export class SilenceDetector {
  private lastSound: number;
  private lastChange: number;
  private text = "";
  private readonly afterMs: number;
  private readonly stalledMs: number;

  constructor(now: number, afterMs = AUTO_SEND_SILENCE_MS) {
    this.afterMs = afterMs;
    this.stalledMs = afterMs + STALLED_EXTRA_MS;
    this.lastSound = now;
    this.lastChange = now;
  }

  audio(samples: Float32Array, now: number): void {
    if (rms(samples) >= QUIET_RMS) this.lastSound = now;
  }

  partial(text: string, now: number): void {
    const words = text.trim();
    if (words && words !== this.text) {
      this.text = words;
      this.lastChange = now;
      this.lastSound = Math.max(this.lastSound, now); // words just arrived: not silent yet
    }
  }

  done(now: number): boolean {
    if (!this.text) return false;
    return now - this.lastSound >= this.afterMs || now - this.lastChange >= this.stalledMs;
  }
}
