import { useEffect, useRef } from "react";
import { messageText, type UiMessage } from "../chatState";
import type { SkillNote } from "../contracts";
import { formatUsd } from "../format";
import { ToolActivity, type ApprovalHandlers } from "./ToolActivity";

const STOP_NOTES: Record<string, string> = {
  refusal: "declined by safety filter",
  max_tokens: "cut off (length limit)",
  loop_detected: "stopped: the same action kept repeating",
  step_limit: "stopped: step limit reached",
  budget: "stopped: token budget for this turn reached",
  skill_failed: "skill stopped early",
};

interface MetaProps {
  message: UiMessage;
  onAskModel: (text: string) => void;
  onSpeak: (message: UiMessage) => void;
  busy: boolean;
}

function SkillLine({ note }: { note: SkillNote }) {
  const left = Math.max(0, note.needed - note.successes);
  const text = {
    learned: `Saved as a possible skill. ${left} more successful run${left === 1 ? "" : "s"} and it replays instantly, at no AI cost.`,
    progress: `${note.successes} of ${note.needed} successful runs of this skill.`,
    promoted: "Learned a skill: next time it replays instantly, at no AI cost.",
    replayed: "",
    failed: "This skill failed this time.",
    demoted: "This skill failed twice, so it won't run on its own until you re-enable it (Skills).",
  }[note.event];
  if (!text) return null;
  return (
    <span className={`meta skill-note skill-${note.event}`}>
      {text}
      <span className="tag-skill">{note.name}</span>
    </span>
  );
}

function ReadAloud({ message, onSpeak, first }: { message: UiMessage; onSpeak: (m: UiMessage) => void; first?: boolean }) {
  return (
    <>
      {!first && " · "}
      <button type="button" className="link" onClick={() => onSpeak(message)}>
        Read aloud
      </button>
    </>
  );
}

function Meta({ message, onAskModel, onSpeak, busy }: MetaProps) {
  if (message.status === "thinking") return <span className="meta">Thinking…</span>;
  if (message.status === "cancelled") return <span className="meta">Stopped</span>;
  if (message.status === "error") return <span className="meta error">{message.error}</span>;
  if (!message.meta) {
    // A saved reply (history): no stats, but it can still be read aloud.
    return message.status === "done" ? (
      <span className="meta">
        <ReadAloud message={message} onSpeak={onSpeak} first />
      </span>
    ) : null;
  }
  const { model, usage, cost_usd, first_token_ms, latency_ms, stop_reason, route, skill, escalated } = message.meta;
  if (route === "instant" || route === "skill") {
    return (
      <span className="meta">
        <span className="instant">{route === "skill" ? "Skill" : "Instant"}</span> · no AI used ·{" "}
        {Math.round(latency_ms)} ms
        {skill && route === "skill" && skill.event !== "replayed" && (
          <>
            {" · "}
            <SkillLine note={skill} />
          </>
        )}
        {message.prompt && (
          <>
            {" · "}
            <button type="button" className="link" disabled={busy} onClick={() => onAskModel(message.prompt!)}>
              Ask Claude instead
            </button>
          </>
        )}
        <ReadAloud message={message} onSpeak={onSpeak} />
      </span>
    );
  }
  const tokens = usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens;
  return (
    <span className="meta">
      {model} · {tokens.toLocaleString()} tokens · {formatUsd(cost_usd)}
      {first_token_ms !== null && ` · first token ${Math.round(first_token_ms)} ms`} · {Math.round(latency_ms)} ms
      {STOP_NOTES[stop_reason] && ` · ${STOP_NOTES[stop_reason]}`}
      {escalated && ` · moved up to Balanced (${escalated})`}
      <ReadAloud message={message} onSpeak={onSpeak} />
      {skill && (
        <>
          <br />
          <SkillLine note={skill} />
        </>
      )}
    </span>
  );
}

interface ChatViewProps extends ApprovalHandlers {
  messages: UiMessage[];
  onAskModel: (text: string) => void;
  onSpeak: (message: UiMessage) => void;
  onEdit: (text: string) => void;
  busy: boolean;
}

export function ChatView({ messages, onDecide, onAskModel, onSpeak, onEdit, busy }: ChatViewProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const last = messages.at(-1);
  const lastSize = last ? messageText(last).length + last.parts.length : 0;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, lastSize]);

  if (messages.length === 0) {
    return (
      <div className="empty">
        <p>Ask anything. Replies stream as they are written.</p>
        <p className="hint">Enter to send · Shift+Enter for a new line · Esc to stop · Ctrl+Shift+K stops everything</p>
      </div>
    );
  }

  return (
    <div className="messages" role="log" aria-live="polite" aria-relevant="additions text">
      {messages.map((m) => (
        <article key={m.id} className={`msg msg-${m.role}`} aria-label={m.role === "user" ? "You" : "Assistant"}>
          {m.parts.length === 0 && m.status === "streaming" && <div className="bubble">…</div>}
          {m.parts.map((p, i) =>
            p.kind === "text" ? (
              <div key={i} className="bubble">
                {p.text}
              </div>
            ) : (
              <ToolActivity key={p.callId} part={p} onDecide={onDecide}
                            onEdit={m.prompt ? () => onEdit(m.prompt as string) : undefined} />
            ),
          )}
          {m.target && (
            <span className="meta target" title="What “this” and “here” referred to">
              ◎ {m.target}
            </span>
          )}
          {m.role === "assistant" && <Meta message={m} onAskModel={onAskModel} onSpeak={onSpeak} busy={busy} />}
        </article>
      ))}
      <div ref={endRef} />
    </div>
  );
}
