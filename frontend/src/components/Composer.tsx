import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { MAX_CHAT_TEXT } from "../contracts";
import type { MicState } from "../voice/useVoice";

export type Attach = "none" | "screen" | "select";

export interface VoiceControls {
  mic: MicState;
  listening: "chat" | "dictate_composer" | "dictate_app" | null;
  partial: string;
  onTalk: () => void;
  onDictate: () => void;
}

interface Props {
  appName: string;
  busy: boolean;
  disabled: boolean;
  onSubmit: (text: string, attach: Attach, background: boolean) => boolean;
  onCancel: () => void;
  voice: VoiceControls;
  /** Dictated text to drop into the box (a new object each time). */
  insert: { text: string } | null;
}

const MIC = (
  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
    <rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" />
    <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" strokeLinecap="round" />
  </svg>
);

export function Composer({ appName, busy, disabled, onSubmit, onCancel, voice, insert }: Props) {
  const [text, setText] = useState("");
  const [attach, setAttach] = useState<Attach>("none");
  const [background, setBackground] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  // New dictation arrived: append it (state adjusted during render, no extra effect pass).
  const [lastInsert, setLastInsert] = useState(insert);
  if (insert !== lastInsert) {
    setLastInsert(insert);
    if (insert?.text) setText((prev) => (prev.trim() ? `${prev.trimEnd()} ${insert.text}` : insert.text));
  }

  const submit = () => {
    const trimmed = text.trim();
    // A background task doesn't wait for the current reply.
    if (!trimmed || (busy && !background) || disabled) return;
    if (onSubmit(trimmed, background ? "none" : attach, background)) {
      setText("");
      setAttach("none");
      setBackground(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && busy) {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="attach" role="group" aria-label="Attach a screenshot">
        <button
          type="button"
          aria-pressed={attach === "screen"}
          disabled={background}
          title="Send a screenshot of your screen with this message"
          onClick={() => setAttach(attach === "screen" ? "none" : "screen")}
        >
          Show screen
        </button>
        <button
          type="button"
          aria-pressed={attach === "select"}
          disabled={background}
          title="Pick a window or region to send with this message"
          onClick={() => setAttach(attach === "select" ? "none" : "select")}
        >
          Pick region
        </button>
        <label className="radio" title="Run this as a background task: keep chatting while it works">
          <input type="checkbox" checked={background} onChange={(e) => setBackground(e.target.checked)} />
          In background
        </label>
        <span className="attach-spacer" />
        <button
          type="button"
          className={voice.listening === "dictate_composer" ? "mic recording" : "mic"}
          aria-pressed={voice.listening === "dictate_composer"}
          title="Dictate into this box"
          onClick={voice.onDictate}
          disabled={disabled}
        >
          {voice.listening === "dictate_composer" ? "Done" : "Dictate"}
        </button>
      </div>
      {voice.listening && (
        <p className="listening" role="status" aria-live="polite">
          <span className="rec-dot" aria-hidden="true" />
          {voice.listening === "dictate_app" ? "Dictating into your app… " : "Listening… "}
          <span className="partial">{voice.partial}</span>
        </p>
      )}
      <label htmlFor="composer-input" className="sr-only">
        Message
      </label>
      <div className="composer-box">
      <textarea
        id="composer-input"
        ref={ref}
        rows={2}
        value={text}
        maxLength={MAX_CHAT_TEXT}
        placeholder={
          disabled
            ? "Waiting for the daemon…"
            : attach === "none"
              ? background
                ? "Task to run in the background, e.g. “check which of my repos have open PRs”"
                : `Ask ${appName}`
              : attach === "screen"
                ? "Ask about your screen, e.g. “where do I click to change the theme?”"
                : "Ask about the region you'll pick"
        }
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className={voice.listening === "chat" ? "mic recording" : "mic"}
        aria-pressed={voice.listening === "chat"}
        title="Talk (or hold Ctrl+Space)"
        onClick={voice.onTalk}
        disabled={disabled}
      >
        {MIC}
        <span>{voice.listening === "chat" ? "Stop" : "Talk"}</span>
      </button>
      {busy && !background ? (
        <button type="button" className="stop" onClick={onCancel}>
          Stop
        </button>
      ) : (
        <button type="submit" className="primary" disabled={disabled || !text.trim()}>
          Send
        </button>
      )}
      </div>
    </form>
  );
}
