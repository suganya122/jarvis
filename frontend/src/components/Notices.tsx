import type { Notify } from "../contracts";

interface Props {
  notices: Notify[];
  onDismiss: (notifyId: string) => void;
  onOpenTask: () => void;
}

/** Reminders, watcher hits and finished tasks. Polite live region: announced, never steals focus. */
export function Notices({ notices, onDismiss, onOpenTask }: Props) {
  return (
    <div className="notices" role="status" aria-live="polite">
      {notices.map((n) => (
        <div key={n.notify_id} className={`notice notice-${n.kind}`}>
          <strong>{n.title}</strong>
          {n.body && <p>{n.body}</p>}
          <div className="row">
            {n.task_id && (
              <button type="button" className="link" onClick={onOpenTask}>
                Show task
              </button>
            )}
            <button type="button" className="link" onClick={() => onDismiss(n.notify_id)}>
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
