import { describe, expect, it } from "vitest";
import { base64, CHUNK_SAMPLES, ChunkBuffer, resample, toPcm16 } from "./pcm";

describe("pcm helpers", () => {
  it("downsamples 48 kHz to 16 kHz by averaging", () => {
    const input = new Float32Array([0.3, 0.3, 0.3, -0.6, -0.6, -0.6]);
    expect(Array.from(resample(input, 48_000))).toEqual([expect.closeTo(0.3), expect.closeTo(-0.6)]);
    expect(resample(new Float32Array(4800), 48_000).length).toBe(1600);
  });

  it("clips and converts to 16-bit", () => {
    expect(Array.from(toPcm16(new Float32Array([0, 1, -1, 2, -2])))).toEqual([0, 32767, -32768, 32767, -32768]);
  });

  it("base64-encodes little-endian PCM", () => {
    expect(base64(new Int16Array([1, 256]))).toBe(btoa(String.fromCharCode(1, 0, 0, 1)));
  });

  it("hands out exact 100 ms chunks and flushes the rest", () => {
    const buffer = new ChunkBuffer();
    expect(buffer.push(new Float32Array(1000))).toHaveLength(0);
    const ready = buffer.push(new Float32Array(2500));
    expect(ready.map((c) => c.length)).toEqual([CHUNK_SAMPLES, CHUNK_SAMPLES]);
    expect(buffer.flush()?.length).toBe(3500 - 2 * CHUNK_SAMPLES);
    expect(buffer.flush()).toBeNull();
  });
});
