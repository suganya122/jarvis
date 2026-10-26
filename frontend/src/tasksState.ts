import { useCallback, useEffect, useReducer } from "react";
import type { Api } from "./api";
import type { ApprovalRequested, Notify, ServerMessage, TaskInfo } from "./contracts";

export interface TasksState {
  tasks: TaskInfo[];
  /** Pending approvals of background tasks, by task id. */
  approvals: Record<string, ApprovalRequested>;
  notices: Notify[];
}

export type TasksAction =
  | { kind: "loaded"; tasks: TaskInfo[] }
  | { kind: "server"; message: ServerMessage }
  | { kind: "dismiss"; notifyId: string };

export const initialTasksState: TasksState = { tasks: [], approvals: {}, notices: [] };

const LIVE = new Set<TaskInfo["status"]>(["queued", "running", "waiting_approval"]);
const MAX_NOTICES = 4;

export function isLive(task: TaskInfo): boolean {
  return LIVE.has(task.status);
}

export function liveCount(state: TasksState): number {
  return state.tasks.filter(isLive).length;
}

function upsert(tasks: TaskInfo[], task: TaskInfo): TaskInfo[] {
  const rest = tasks.filter((t) => t.task_id !== task.task_id);
  return [task, ...rest].sort((a, b) => b.created_at - a.created_at);
}

const INTERRUPTED_NOTICE = "interrupted";

/** The daemon announces interrupted tasks at startup, before any tab reconnects; derive it from the list too. */
function withInterrupted(notices: Notify[], tasks: TaskInfo[]): Notify[] {
  const rest = notices.filter((n) => n.notify_id !== INTERRUPTED_NOTICE && !n.title.endsWith("interrupted"));
  const interrupted = tasks.filter((t) => t.status === "interrupted");
  if (interrupted.length === 0) return rest;
  const n = interrupted.length;
  const notice: Notify = {
    type: "notify",
    notify_id: INTERRUPTED_NOTICE,
    title: `${n} background task${n > 1 ? "s" : ""} interrupted`,
    body: "Open Tasks to resume or discard.",
    kind: "task",
    task_id: interrupted[0].task_id,
  };
  return [notice, ...rest].slice(0, MAX_NOTICES);
}

export function tasksReducer(state: TasksState, action: TasksAction): TasksState {
  if (action.kind === "loaded") {
    const waiting = new Set(action.tasks.filter((t) => t.status === "waiting_approval").map((t) => t.task_id));
    const approvals = Object.fromEntries(Object.entries(state.approvals).filter(([id]) => waiting.has(id)));
    return { tasks: action.tasks, approvals, notices: withInterrupted(state.notices, action.tasks) };
  }
  if (action.kind === "dismiss") return { ...state, notices: state.notices.filter((n) => n.notify_id !== action.notifyId) };
  const msg = action.message;
  switch (msg.type) {
    case "task.update": {
      const approvals = { ...state.approvals };
      if (msg.task.status !== "waiting_approval") delete approvals[msg.task.task_id];
      return { ...state, tasks: upsert(state.tasks, msg.task), approvals };
    }
    case "approval.request":
      if (!state.tasks.some((t) => t.task_id === msg.request_id)) return state;
      return { ...state, approvals: { ...state.approvals, [msg.request_id]: msg } };
    case "approval.resolved": {
      const entry = Object.entries(state.approvals).find(([, a]) => a.approval_id === msg.approval_id);
      if (!entry) return state;
      const approvals = { ...state.approvals };
      delete approvals[entry[0]];
      return { ...state, approvals };
    }
    case "notify":
      return { ...state, notices: [msg, ...state.notices].slice(0, MAX_NOTICES) };
    default:
      return state;
  }
}

/** Shows a desktop notification through the browser too, when the tab is hidden and the user allowed it. */
function browserNotify(msg: Notify): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted" || !document.hidden) return;
  try {
    new Notification(msg.title, { body: msg.body, tag: msg.notify_id });
  } catch {
    // Some browsers only allow notifications from a service worker; the in-page toast still shows.
  }
}

export function useTasks(api: Api) {
  const [state, dispatch] = useReducer(tasksReducer, initialTasksState);

  const reload = useCallback(() => {
    api.tasks().then((tasks) => dispatch({ kind: "loaded", tasks }), () => {});
  }, [api]);

  useEffect(reload, [reload]);

  const handle = useCallback((message: ServerMessage) => {
    dispatch({ kind: "server", message });
    if (message.type === "notify") browserNotify(message);
  }, []);

  const dismiss = useCallback((notifyId: string) => dispatch({ kind: "dismiss", notifyId }), []);

  return { state, handle, dismiss, reload };
}
