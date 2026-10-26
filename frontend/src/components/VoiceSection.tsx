import { useCallback, useEffect, useState } from "react";
import type { Api } from "../api";
import type { DownloadProgress, VoiceStatus } from "../contracts";
import type { MicState } from "../voice/useVoice";
import { HandsFreeSection } from "./HandsFreeSection";

export interface VoiceSettings {
  speak_replies: boolean;
  speak_chat_replies: boolean;
  overlay: boolean;
  speculative: boolean;
  auto_send_silence: boolean;
  hands_free: boolean;
  wake_word: string;
  follow_up_s: number;
  dictation_cleanup: "llm" | "local" | "off";
  [key: string]: unknown;
}

interface Props {
  api: Api;
  voice: VoiceSettings;
  downloads: Record<string, DownloadProgress>;
  mic: MicState;
  onSave: (voice: VoiceSettings) => void;
  onReleaseMic: () => void;
  /** The typing box (off by default; kept in this browser). */
  typing: boolean;
  onTyping: (on: boolean) => void;
}

const MB = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;

export function VoiceSection({ api, voice, downloads, mic, onSave, onReleaseMic, typing, onTyping }: Props) {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const refresh = useCallback(() => {
    api.voice().then(setStatus, () => setStatus(null));
  }, [api]);

  useEffect(() => refresh(), [refresh]);
  useEffect(() => {
    const timer = setInterval(refresh, 3000); // the hands-free state changes on its own
    return () => clearInterval(timer);
  }, [refresh]);
  const finished = Object.values(downloads).filter((d) => d.state === "done" || d.state === "error").length;
  useEffect(() => refresh(), [finished, refresh]);

  const set = (patch: Partial<VoiceSettings>) => onSave({ ...voice, ...patch });

  return (
    <section aria-labelledby="voice-title">
      <h3 id="voice-title">Voice</h3>
      <p className="hint">
        Speech is recognised on this computer by a free, open model. Your voice is never sent anywhere.
      </p>
      <HandsFreeSection key={voice.wake_word} voice={voice} status={status?.hands_free ?? null} set={set} />
      <ul className="models">
        {status?.models.map((m) => {
          const progress = downloads[m.id];
          const busy = m.downloading || (progress && !["done", "error"].includes(progress.state));
          return (
            <li key={m.id} className="model">
              <div className="row">
                <strong>{m.title}</strong>
                {m.recommended && <span className="badge">Recommended for this computer</span>}
                {m.active && <span className="badge badge-active">In use</span>}
              </div>
              <p className="hint">
                {MB(m.size)} download · license {m.license}
              </p>
              {busy && progress && (
                <div className="progress-row">
                  <progress max={progress.total} value={progress.done} aria-label={`Downloading ${m.title}`} />
                  <span className="hint">
                    {progress.state === "downloading" ? `${MB(progress.done)} of ${MB(progress.total)}` : `${progress.state}…`}
                  </span>
                </div>
              )}
              {progress?.state === "error" && <p className="error-line">{progress.error}</p>}
              <div className="row">
                {!m.installed && !busy && (
                  <button type="button" className="primary" onClick={() => void api.downloadModel(m.id).then(refresh)}>
                    Download {MB(m.size)}
                  </button>
                )}
                {m.installed && !m.active && (
                  <button type="button" onClick={() => void api.useModel(m.id).then(refresh)}>
                    Use this one
                  </button>
                )}
                {m.installed && (
                  <button type="button" className="danger-link" onClick={() => void api.deleteModel(m.id).then(refresh)}>
                    Remove
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="stack">
        <label className="radio">
          <input type="checkbox" checked={typing} onChange={(e) => onTyping(e.target.checked)} />
          Show a typing box too (for when you can't talk). Off: this page only shows what it's doing
        </label>
        <label className="radio">
          <input type="checkbox" checked={voice.overlay} onChange={(e) => set({ overlay: e.target.checked })} />
          Show the voice overlay: a small window on top of other apps while a voice request runs
        </label>
        {status && voice.overlay && (
          <p className="hint" role="status">
            Overlay:{" "}
            {status.overlay.available === null
              ? "starting…"
              : status.overlay.available
                ? `ready (${status.overlay.detail})`
                : `not available: ${status.overlay.detail}. Voice still works in this window.`}
          </p>
        )}
        <label className="radio">
          <input type="checkbox" checked={voice.auto_send_silence}
                 onChange={(e) => set({ auto_send_silence: e.target.checked })} />
          Send when I stop talking (2 seconds of silence). Off: press the shortcut again, or click Stop
        </label>
        <label className="radio">
          <input type="checkbox" checked={voice.speculative} onChange={(e) => set({ speculative: e.target.checked })} />
          Start thinking while I pause (faster replies; nothing runs until I finish)
        </label>
        <label>
          Dictation clean-up{" "}
          <select
            value={voice.dictation_cleanup}
            onChange={(e) => set({ dictation_cleanup: e.target.value as VoiceSettings["dictation_cleanup"] })}
          >
            <option value="llm">Smart (one quick, cheap AI pass; falls back to basic)</option>
            <option value="local">Basic (on this computer only)</option>
            <option value="off">Off (exact words)</option>
          </select>
        </label>
      </div>
      {status && (
        <details className="shortcuts">
          <summary>Use voice from any app (keyboard shortcuts)</summary>
          <p className="hint">
            Add these as custom shortcuts in your desktop's keyboard settings (GNOME: Settings → Keyboard → View and
            Customize Shortcuts → Custom Shortcuts). Keep this page open in a tab with the microphone enabled.
          </p>
          <dl>
            {Object.entries(status.shortcut_commands).map(([name, command]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>
                  <code>{command}</code>{" "}
                  <button type="button" onClick={() => void navigator.clipboard?.writeText(command)}>
                    Copy
                  </button>
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {(mic === "ready" || mic === "recording") && (
        <p className="hint">
          Microphone is enabled for quick starts (nothing is sent until you talk).{" "}
          <button type="button" className="link" onClick={onReleaseMic}>
            Turn it off
          </button>
        </p>
      )}
    </section>
  );
}
