import { useState } from "react";
import type { HandsFreeStatus } from "../contracts";
import type { VoiceSettings } from "./VoiceSection";

const STATE_TEXT: Record<HandsFreeStatus["state"], string> = {
  off: "Off",
  idle: "Waiting",
  listening: "Listening to you",
  thinking: "Working on it",
  speaking: "Speaking",
  approval: "Waiting for your OK",
};
const WAKE_WORD = /^[A-Za-z][A-Za-z' ]{1,29}$/;

interface Props {
  voice: VoiceSettings;
  status: HandsFreeStatus | null;
  set: (patch: Partial<VoiceSettings>) => void;
}

/** Say the wake word, talk, hear the answer: no window, no clicks. Remount (key) when the saved wake word changes. */
export function HandsFreeSection({ voice, status, set }: Props) {
  const [wake, setWake] = useState(voice.wake_word);
  const wakeValid = WAKE_WORD.test(wake.trim());

  return (
    <div className="stack hands-free">
      <label className="radio">
        <input type="checkbox" checked={voice.hands_free} onChange={(e) => set({ hands_free: e.target.checked })} />
        Hands-free: listen for my wake word, then talk with me (no clicks)
      </label>
      {status && (
        <p className="hint" role="status">
          {STATE_TEXT[status.state]}: {status.detail}
        </p>
      )}
      <div className="row">
        <label>
          Wake word{" "}
          <input
            value={wake}
            aria-invalid={!wakeValid}
            aria-describedby="wake-hint"
            onChange={(e) => setWake(e.target.value)}
            onBlur={() => wakeValid && wake.trim().toLowerCase() !== voice.wake_word && set({ wake_word: wake.trim().toLowerCase() })}
          />
        </label>
        <label>
          Keep listening after a reply{" "}
          <select value={voice.follow_up_s} onChange={(e) => set({ follow_up_s: Number(e.target.value) })}>
            {[5, 8, 12, 20].map((s) => (
              <option key={s} value={s}>
                {s} seconds
              </option>
            ))}
          </select>
        </label>
      </div>
      <p id="wake-hint" className={wakeValid ? "hint" : "error-line"}>
        {wakeValid
          ? `Say “${voice.wake_word}, what time is it?” or just “${voice.wake_word}” and wait for “Yes?”. Say “that's all” to finish.`
          : "Use letters only, 2 to 30 of them."}
      </p>
      <p className="hint">
        Everything you say is turned into text on this computer and thrown away unless it starts with the wake word.
        Pause everything turns the microphone off. Ordinary changes can be answered “yes” or “no”; sending and buying
        always need a click.
      </p>
      {status && (
        <p className="hint">
          Voice: {status.voice}.
          {!status.voice.startsWith("natural") && (
            <>
              {" "}
              For a natural voice (a one-time download of about 100 MB), run this in a terminal:{" "}
              <code className="command">{status.natural_voice_command}</code>
            </>
          )}
        </p>
      )}
    </div>
  );
}
