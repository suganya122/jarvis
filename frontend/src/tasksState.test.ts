import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState } from "./chatState";
import type { ApprovalRequested, ServerMessage, TaskInfo } from "./contracts";
import { initialTasksState, liveCount, tasksReducer, type TasksState } from "./tasksState";

const task = (over: Partial<TaskInfo> = {}): TaskInfo => ({
  task_id: "t1",
  session_id: null,
  prompt: "tidy downloads",
  origin: "user",
  status: "queued",
  step: 0,
  preview: "",
  current_tool: "",
  tokens: 0,
  cost_usd: 0,
  error: "",
  created_at: 1,
  finished_at: null,
  ...over,
});

const approval: ApprovalRequested = {
  type: "approval.request",
  approval_id: "a1",
  request_id: "t1",
  call_id: "c1",
  tool: "note",
  risk: "write",
  summary: "note x",
  reason: "fs.write is set to ask",
  args: {},
  untrusted_context: false,
  can_remember: true,
  expires_in_s: 120,
};

const server = (state: TasksState, message: ServerMessage) => tasksReducer(state, { kind: "server", message });

describe("tasksReducer", () => {
  it("tracks live status, newest first", () => {
    let s = server(initialTasksState, { type: "task.update", task: task() });
    s = server(s, { type: "task.update", task: task({ task_id: "t2", created_at: 2, status: "running" }) });
    expect(s.tasks.map((t) => t.task_id)).toEqual(["t2", "t1"]);
    expect(liveCount(s)).toBe(2);
    s = server(s, { type: "task.update", task: task({ status: "done", preview: "All tidy." }) });
    expect(s.tasks).toHaveLength(2);
    expect(s.tasks[1].preview).toBe("All tidy.");
    expect(liveCount(s)).toBe(1);
  });

  it("keeps approvals for its own tasks only and clears them when answered", () => {
    let s = server(initialTasksState, approval);
    expect(s.approvals).toEqual({}); // not a background task
    s = server(s, { type: "task.update", task: task({ status: "waiting_approval" }) });
    s = server(s, approval);
    expect(s.approvals.t1.approval_id).toBe("a1");
    s = server(s, { type: "approval.resolved", approval_id: "a1", approved: true, reason: "Approved" });
    expect(s.approvals).toEqual({});
    s = server(s, approval);
    s = server(s, { type: "task.update", task: task({ status: "cancelled" }) });
    expect(s.approvals).toEqual({});
  });

  it("collects notices, capped, and dismisses them", () => {
    let s = initialTasksState;
    for (let i = 0; i < 6; i++) {
      s = server(s, { type: "notify", notify_id: `n${i}`, title: `T${i}`, body: "", kind: "reminder" });
    }
    expect(s.notices.map((n) => n.notify_id)).toEqual(["n5", "n4", "n3", "n2"]);
    s = tasksReducer(s, { kind: "dismiss", notifyId: "n4" });
    expect(s.notices).toHaveLength(3);
  });

  it("raises one notice for interrupted tasks found on reconnect", () => {
    const loaded = (tasks: TaskInfo[]) => (s: TasksState) => tasksReducer(s, { kind: "loaded", tasks });
    let s = loaded([task({ status: "interrupted" }), task({ task_id: "t2", status: "interrupted" })])(initialTasksState);
    expect(s.notices.map((n) => n.title)).toEqual(["2 background tasks interrupted"]);
    s = loaded([task({ status: "interrupted" })])(s);
    expect(s.notices.map((n) => n.title)).toEqual(["1 background task interrupted"]);
    s = loaded([task({ status: "done" })])(s);
    expect(s.notices).toEqual([]);
  });
});

describe("chatReducer with background tasks running", () => {
  it("ignores events for turns this tab didn't start", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "hi", tier: "cheap" });
    s = chatReducer(s, {
      kind: "server",
      message: { type: "chat.started", request_id: "r1", trace_id: "t", session_id: "mine", tier: "cheap" },
    });
    const before = s;
    const foreign: ServerMessage[] = [
      { type: "chat.started", request_id: "bg", trace_id: "t", session_id: "theirs", tier: "cheap" },
      { type: "chat.delta", request_id: "bg", text: "background text" },
      { type: "tool.started", request_id: "bg", call_id: "c9", tool: "peek", summary: "peek", risk: "read" },
      { ...approval, request_id: "bg" },
      { type: "chat.cancelled", request_id: "bg" },
      { type: "chat.error", request_id: "bg", code: "x", message: "boom" },
    ];
    for (const message of foreign) s = chatReducer(s, { kind: "server", message });
    expect(s).toBe(before);
    expect(s.sessionId).toBe("mine");
    expect(s.pendingRequestId).toBe("r1");
  });
});
