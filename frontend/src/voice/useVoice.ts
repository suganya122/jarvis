import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMessage, ServerMessage, VoiceMode } from "../contracts";
import { base64, ChunkBuffer, resample, TARGET_RATE, toPcm16 } from "./pcm";
import { SilenceDetector } from "./silence";
import { stopSpeaking } from "./speech";

export type MicState = "off" | "starting" | "ready" | "recording" | "denied";

export interface VoiceFinalInfo {
  streamId: string;
  text: string;
  mode: VoiceMode;
  releaseToFinalMs: number;
  hotkeyToFirstPartialMs: number | null;
}

interface Options {
  send: (msg: ClientMessage) => boolean;
  sessionId: string | null;
  onFinal: (info: VoiceFinalInfo) => void;
  /** Send a spoken request after 2 s of silence (chat mode only; dictation waits for you). */
  autoSend: boolean;
}

/**
 * Microphone -> 16 kHz PCM -> the local daemon. The mic stays "warm" (open but silent)
 * once enabled, so a press starts instantly; audio is only sent while recording.
 */
export function useVoice({ send, sessionId, onFinal, autoSend }: Options) {
  const [mic, setMic] = useState<MicState>("off");
  const [partial, setPartial] = useState("");
  const [mode, setMode] = useState<VoiceMode>("chat");
  const [error, setError] = useState<string | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  // The current stream: `capturing` while the key is held; kept after release until its final text.
  const recording = useRef<{
    id: string;
    started: number;
    firstPartial: number | null;
    capturing: boolean;
    silence: SilenceDetector | null;
  } | null>(null);
  const buffer = useRef(new ChunkBuffer());
  const finalRef = useRef(onFinal);
  const session = useRef(sessionId);
  const autoSendRef = useRef(autoSend);
  const stopRef = useRef<() => void>(() => {});

  useEffect(() => {
    finalRef.current = onFinal;
    session.current = sessionId;
    autoSendRef.current = autoSend;
  }, [onFinal, sessionId, autoSend]);

  const enable = useCallback(async (): Promise<boolean> => {
    if (ctx.current) return true;
    setMic("starting");
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const audio = new AudioContext();
      await audio.audioWorklet.addModule("/pcm-worklet.js");
      const node = new AudioWorkletNode(audio, "pcm-tap");
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        const rec = recording.current;
        if (!rec?.capturing) return;
        const now = performance.now();
        rec.silence?.audio(event.data, now);
        if (rec.silence?.done(now)) {
          stopRef.current(); // finished speaking: send it, as if the shortcut was pressed again
          return;
        }
        for (const chunk of buffer.current.push(resample(event.data, audio.sampleRate, TARGET_RATE))) {
          send({ type: "voice.audio", stream_id: rec.id, pcm_b64: base64(toPcm16(chunk)) });
        }
      };
      audio.createMediaStreamSource(media).connect(node);
      ctx.current = audio;
      stream.current = media;
      setMic("ready");
      setError(null);
      return true;
    } catch {
      setMic("denied");
      setError("Microphone access was blocked. Allow it for this page in the browser's site settings.");
      return false;
    }
  }, [send]);

  const release = useCallback(() => {
    recording.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    void ctx.current?.close();
    ctx.current = null;
    stream.current = null;
    setMic("off");
  }, []);

  const start = useCallback(
    async (nextMode: VoiceMode, pressedAt = performance.now()) => {
      if (recording.current?.capturing) return;
      stopSpeaking(); // pressing the talk key interrupts a spoken reply
      if (!(await enable())) return;
      await ctx.current?.resume();
      const id = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
      buffer.current = new ChunkBuffer();
      const silence = nextMode === "chat" && autoSendRef.current ? new SilenceDetector(performance.now()) : null;
      recording.current = { id, started: pressedAt, firstPartial: null, capturing: true, silence };
      setMode(nextMode);
      setPartial("");
      send({ type: "voice.start", stream_id: id, sample_rate: TARGET_RATE, mode: nextMode, session_id: session.current });
      setMic("recording");
    },
    [enable, send],
  );

  const stop = useCallback(
    (cancel = false) => {
      const rec = recording.current;
      if (!rec?.capturing) return;
      rec.capturing = false; // no more audio for this stream
      const rest = buffer.current.flush();
      if (rest && !cancel) send({ type: "voice.audio", stream_id: rec.id, pcm_b64: base64(toPcm16(rest)) });
      send({ type: "voice.stop", stream_id: rec.id, cancel });
      setMic(ctx.current ? "ready" : "off");
      if (cancel) recording.current = null;
    },
    [send],
  );

  useEffect(() => {
    stopRef.current = () => stop();
  }, [stop]);

  const toggle = useCallback(
    (nextMode: VoiceMode) => {
      if (mic === "recording") stop();
      else void start(nextMode);
    },
    [mic, start, stop],
  );

  /** Feed server messages here. */
  const handle = useCallback((msg: ServerMessage) => {
    const rec = recording.current;
    if (msg.type === "voice.partial" && rec && msg.stream_id === rec.id) {
      if (rec.firstPartial === null) rec.firstPartial = performance.now();
      rec.silence?.partial(msg.text, performance.now());
      setPartial(msg.text);
    } else if (msg.type === "voice.final" && rec && msg.stream_id === rec.id) {
      recording.current = null;
      setPartial("");
      finalRef.current({
        streamId: msg.stream_id,
        text: msg.text,
        mode: msg.mode,
        releaseToFinalMs: msg.release_to_final_ms,
        hotkeyToFirstPartialMs: rec.firstPartial === null ? null : rec.firstPartial - rec.started,
      });
    } else if (msg.type === "voice.error") {
      recording.current = null;
      setPartial("");
      setError(msg.message);
      setMic(ctx.current ? "ready" : "off");
    }
  }, []);

  useEffect(() => release, [release]);

  return { mic, partial, mode, error, start, stop, toggle, release, enable, handle, clearError: () => setError(null) };
}
