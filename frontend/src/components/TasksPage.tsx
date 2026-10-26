import { Page } from "./Page";
import type { ApprovalRequested, TaskInfo } from "../contracts";
import { formatUsd } from "../format";
import { isLive } from "../tasksState";

const STATUS: Record<TaskInfo["status"], string> = {
  queued: "Waiting to start",
  running: "Running",
  waiting_approval: "Needs your OK",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

function origin(task: TaskInfo): string {
  if (task.origin.startsWith("routine:")) return "From a routine";
  if (task.origin.startsWith("watcher:")) return "From a watcher";
  return "You started it";
}

interface Props {
  tasks: TaskInfo[];
  approvals: Record<string, ApprovalRequested>;
  onClose: () => void;
  onControl: (taskId: string, action: "cancel" | "resume" | "discard") => void;
  onDecide: (approvalId: string, approved: boolean) => void;
  onOpenChat: (sessionId: string) => void;
}

function Approval({ approval, onDecide }: { approval: ApprovalRequested; onDecide: Props["onDecide"] }) {
  return (
    <section className={`approval ${approval.risk === "read" || approval.risk === "write" ? "" : "approval-high"}`}>
      <h4 className="approval-title">Allow {approval.tool}?</h4>
      <p className="approval-summary">{approval.summary}</p>
      <p className="hint">Asking because {approval.reason}.</p>
      {approval.untrusted_context && (
        <p className="approval-warn" role="note">
          This task read content from files, pages, or command output. Check this is really what you asked for.
        </p>
      )}
      <div className="row">
        <button type="button" onClick={() => onDecide(approval.approval_id, false)}>
          Deny
        </button>
        <button type="button" className="primary" onClick={() => onDecide(approval.approval_id, true)}>
          Approve
        </button>
      </div>
    </section>
  );
}

function TaskCard({ task, approval, ...props }: Omit<Props, "tasks" | "approvals" | "onClose"> & {
  task: TaskInfo;
  approval?: ApprovalRequested;
}) {
  const titleId = `task-${task.task_id}`;
  return (
    <article className="task" aria-labelledby={titleId} data-status={task.status}>
      <div className="row">
        <span className={`badge badge-task-${task.status}`}>{STATUS[task.status]}</span>
        <h3 id={titleId} className="skill-name">
          {task.prompt}
        </h3>
      </div>
      <p className="hint">
        {origin(task)}
        {task.step > 0 && ` · step ${task.step}`}
        {task.tokens > 0 && ` · ${task.tokens.toLocaleString()} tokens · ${formatUsd(task.cost_usd)}`}
      </p>
      {task.current_tool && isLive(task) && <p className="task-now">Now: {task.current_tool}</p>}
      {task.preview && <p className="task-preview">{task.preview}</p>}
      {task.error && <p className="error-line">{task.error}</p>}
      {task.status === "interrupted" && (
        <p className="hint">The app stopped while this was running. Some steps may already be done.</p>
      )}
      {approval && <Approval approval={approval} onDecide={props.onDecide} />}
      <div className="row">
        {isLive(task) && (
          <button type="button" className="stop" onClick={() => props.onControl(task.task_id, "cancel")}>
            Cancel
          </button>
        )}
        {task.status === "interrupted" && (
          <>
            <button type="button" className="primary" onClick={() => props.onControl(task.task_id, "resume")}>
              Resume
            </button>
            <button type="button" onClick={() => props.onControl(task.task_id, "discard")}>
              Discard
            </button>
          </>
        )}
        {task.session_id && (
          <button type="button" className="link" onClick={() => props.onOpenChat(task.session_id as string)}>
            Open chat
          </button>
        )}
      </div>
    </article>
  );
}

export function TasksPage({ tasks, approvals, onClose, ...actions }: Props) {
  return (
    <Page id="tasks-title" title="Background agents" onClose={onClose}>
      <p className="hint">
        Tasks run on their own while you keep chatting. They never take over your screen: no typing, clicking, or
        opening windows. Each has its own token budget.
      </p>
      {tasks.length === 0 && (
        <p className="state state-empty">No background tasks yet. Tick “In background” before sending a message.</p>
      )}
      {tasks.map((t) => (
        <TaskCard key={t.task_id} task={t} approval={approvals[t.task_id]} {...actions} />
      ))}
    </Page>
  );
}
