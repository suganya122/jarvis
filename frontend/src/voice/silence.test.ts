import { describe, expect, it } from "vitest";
import { rms, SilenceDetector } from "./silence";

const loud = new Float32Array(160).fill(0.2);
const quiet = new Float32Array(160).fill(0.001);

describe("SilenceDetector", () => {
  it("never sends before any words were heard", () => {
    const d = new SilenceDetector(0);
    d.audio(quiet, 5000);
    expect(d.done(10_000)).toBe(false);
  });

  it("sends after 2 s of quiet once words were heard", () => {
    const d = new SilenceDetector(0);
    d.audio(loud, 500);
    d.partial("what time", 600);
    d.audio(loud, 900);
    d.audio(quiet, 1500);
    expect(d.done(2800)).toBe(false); // 1.9 s after the last sound
    expect(d.done(2900)).toBe(true);
  });

  it("keeps listening while you talk, even past 2 s", () => {
    const d = new SilenceDetector(0);
    for (let t = 0; t <= 6000; t += 500) {
      d.audio(loud, t);
      d.partial(`word ${t}`, t);
      expect(d.done(t)).toBe(false);
    }
  });

  it("sends in a noisy room 3 s after the words stop changing", () => {
    const d = new SilenceDetector(0);
    d.partial("open my email", 1000);
    for (let t = 1000; t <= 4000; t += 100) d.audio(loud, t); // a fan: never quiet
    d.partial("open my email", 2500); // same words again don't count as new
    expect(d.done(3900)).toBe(false);
    expect(d.done(4000)).toBe(true);
  });

  it("measures loudness", () => {
    expect(rms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
    expect(rms(new Float32Array())).toBe(0);
  });
});
