import type { SessionInfo } from "../contracts";

interface Props {
  sessions: SessionInfo[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  /** Only with the typing box on: a voice conversation starts itself. */
  onNew?: () => void;
}

export function SessionList({ sessions, activeId, onOpen, onDelete, onNew }: Props) {
  return (
    <nav className="sessions" aria-label="Chats">
      {onNew && (
        <button type="button" className="primary new-chat" onClick={onNew}>
          New chat
        </button>
      )}
      {sessions.length > 0 && <h2>Conversations</h2>}
      <ul>
        {sessions.map((s) => (
          <li key={s.id} className={s.id === activeId ? "active" : undefined}>
            <button
              type="button"
              className="session-title"
              aria-current={s.id === activeId ? "true" : undefined}
              onClick={() => onOpen(s.id)}
            >
              {s.title}
            </button>
            <button
              type="button"
              className="icon"
              aria-label={`Delete chat: ${s.title}`}
              onClick={() => {
                if (window.confirm(`Delete "${s.title}"?`)) onDelete(s.id);
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
