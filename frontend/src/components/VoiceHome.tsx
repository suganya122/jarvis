import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Api } from "../api";
import type { HandsFreeStatus, SessionInfo } from "../contracts";

const HEADLINE: Record<HandsFreeStatus["state"], (wake: string) => string> = {
  off: () => "Voice is off",
  idle: (wake) => `Say “Hey ${wake}”`,
  listening: () => "Live: just talk",
  thinking: () => "Working on it…",
  speaking: () => "Speaking…",
  approval: () => "Waiting for your OK",
};
const POLL_MS = 1000;

interface Props {
  api: Api;
  wakeWord: string;
  sessions: SessionInfo[];
  onOpenHistory: (sessionId: string) => void;
  onOpenSettings: () => void;
  /** A conversation happened (it runs in the assistant, not this page): reload the list. */
  onActivity: () => void;
  banners: ReactNode;
}

/** The home screen: what the voice assistant is doing right now. No typing, no buttons to talk. */
export function VoiceHome({ api, wakeWord, sessions, onOpenHistory, onOpenSettings, onActivity, banners }: Props) {
  const [status, setStatus] = useState<HandsFreeStatus | null>(null);
  const [error, setError] = useState(false);
  const activity = useRef(onActivity);
  const last = useRef<HandsFreeStatus["state"] | null>(null);
  useEffect(() => {
    activity.current = onActivity;
  });
  useEffect(() => {
    let alive = true;
    const poll = () =>
      api.voice().then(
        (v) => {
          if (!alive) return;
          const state = v.hands_free.state;
          if (last.current !== null && last.current !== state && (state === "speaking" || state === "idle")) {
            activity.current(); // a reply is being spoken, or a conversation just ended
          }
          last.current = state;
          setStatus(v.hands_free);
          setError(false);
        },
        () => alive && setError(true),
      );
    void poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [api]);

  const wake = wakeWord.replace(/^./, (c) => c.toUpperCase());
  const state = status?.state ?? "off";
  return (
    <section className="voice-home" aria-labelledby="home-title">
      {banners}
      <div className={`home-orb home-orb-${state}`} aria-hidden="true" />
      <h2 id="home-title">
        {status && wakeWord ? HEADLINE[state](wake) : error ? "Can't reach the assistant" : "Starting…"}
      </h2>
      <p className="hint" role="status" aria-live="polite">
        {state !== "idle" && status?.detail /* idle: the headline already says it */}
        {state === "off" && (
          <>
            {" "}
            <button type="button" className="link" onClick={onOpenSettings}>
              Voice settings
            </button>
          </>
        )}
      </p>
      {wakeWord && (
      <ul className="home-examples" aria-label="Things to say">
        <li>“Hey {wake}, what time is it?”</li>
        <li>“Hey {wake}, book a flight from Chennai to Delhi on Friday.”</li>
        <li>“Hey {wake}, Chrome on the left, Terminal on the right.”</li>
        <li>Then keep talking: it stays live until you say “go offline”.</li>
      </ul>
      )}
      {status && <p className="hint">Voice: {status.voice}. Everything you say is recognised on this computer.</p>}
      {sessions.length > 0 && (
        <nav className="home-recent" aria-label="Recent conversations">
          <h3>Recent conversations</h3>
          <ul>
            {sessions.slice(0, 5).map((s) => (
              <li key={s.id}>
                <button type="button" className="link" onClick={() => onOpenHistory(s.id)}>
                  {s.title}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </section>
  );
}
