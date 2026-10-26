/** Audio helpers: the microphone gives Float32 at 44.1/48 kHz; the engine wants 16 kHz PCM16. */

export const TARGET_RATE = 16_000;
export const CHUNK_SAMPLES = TARGET_RATE / 10; // 100 ms per message

/** Downsample by averaging each output sample's source window (a simple anti-alias filter). */
export function resample(input: Float32Array, inputRate: number, outputRate = TARGET_RATE): Float32Array {
  if (inputRate === outputRate) return input.slice();
  const ratio = inputRate / outputRate;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

export function toPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function base64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** Collects resampled audio and hands out fixed 100 ms chunks. */
export class ChunkBuffer {
  private parts: Float32Array[] = [];
  private size = 0;

  push(samples: Float32Array): Float32Array[] {
    this.parts.push(samples);
    this.size += samples.length;
    const ready: Float32Array[] = [];
    while (this.size >= CHUNK_SAMPLES) ready.push(this.take(CHUNK_SAMPLES));
    return ready;
  }

  flush(): Float32Array | null {
    return this.size > 0 ? this.take(this.size) : null;
  }

  private take(n: number): Float32Array {
    const out = new Float32Array(n);
    let filled = 0;
    while (filled < n) {
      const head = this.parts[0];
      const need = n - filled;
      if (head.length <= need) {
        out.set(head, filled);
        filled += head.length;
        this.parts.shift();
      } else {
        out.set(head.subarray(0, need), filled);
        this.parts[0] = head.subarray(need);
        filled += need;
      }
    }
    this.size -= n;
    return out;
  }
}
