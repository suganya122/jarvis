import type {
  HighlightBox,
  ApprovalRequested,
  ChatDone,
  ModelTier,
  RiskLevel,
  ServerMessage,
  StoredMessage,
  ToolStatus,
} from "./contracts";

export interface PendingApproval {
  approvalId: string;
  reason: string;
  args: Record<string, unknown>;
  untrusted: boolean;
  canRemember: boolean;
  expiresInS: number;
}

export interface ToolPart {
  kind: "tool";
  callId: string;
  tool: string;
  summary: string;
  risk: RiskLevel;
  status: "running" | "waiting" | ToolStatus;
  preview?: string;
  durationMs?: number;
  approval?: PendingApproval;
  decision?: string;
  /** The tool confirmed its effect (e.g. the window really moved). */
  verified?: boolean;
  /** Local-only thumbnail of a screenshot this tool took. */
  image?: string;
  capture?: { id: string; width: number; height: number };
  highlights?: HighlightBox[];
}

export type Part = { kind: "text"; text: string } | ToolPart;

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  parts: Part[];
  status: "streaming" | "thinking" | "done" | "cancelled" | "error";
  meta?: Pick<
    ChatDone,
    "model" | "usage" | "cost_usd" | "latency_ms" | "first_token_ms" | "stop_reason" | "route" | "timings" | "skill" | "escalated"
  >;
  error?: string;
  /** The user text this reply answers (for "Ask Claude instead"). */
  prompt?: string;
  /** User voice messages: the screen and window "this"/"here" meant. */
  target?: string;
}

export interface ChatState {
  sessionId: string | null;
  messages: UiMessage[];
  pendingRequestId: string | null;
  lastError: string | null;
  paused: boolean;
}

export type ChatAction =
  | { kind: "local_send"; requestId: string; text: string; tier: ModelTier }
  | { kind: "server"; message: ServerMessage }
  | { kind: "load_session"; sessionId: string; messages: StoredMessage[] }
  | { kind: "new_session" }
  | { kind: "approval_sent"; approvalId: string; approved: boolean };

export const initialChatState: ChatState = {
  sessionId: null,
  messages: [],
  pendingRequestId: null,
  lastError: null,
  paused: false,
};

export function messageText(message: UiMessage): string {
  return message.parts.map((p) => (p.kind === "text" ? p.text : "")).join("");
}

export function pendingApprovals(state: ChatState): ToolPart[] {
  return state.messages.flatMap((m) => m.parts.filter((p): p is ToolPart => p.kind === "tool" && !!p.approval));
}

function updateAssistant(state: ChatState, requestId: string, patch: (m: UiMessage) => UiMessage): ChatState {
  const id = `a-${requestId}`;
  return { ...state, messages: state.messages.map((m) => (m.id === id ? patch(m) : m)) };
}

function updateTool(state: ChatState, match: (p: ToolPart) => boolean, patch: (p: ToolPart) => ToolPart): ChatState {
  return {
    ...state,
    messages: state.messages.map((m) =>
      m.parts.some((p) => p.kind === "tool" && match(p))
        ? { ...m, parts: m.parts.map((p) => (p.kind === "tool" && match(p) ? patch(p) : p)) }
        : m,
    ),
  };
}

function settled(state: ChatState, requestId: string): string | null {
  return state.pendingRequestId === requestId ? null : state.pendingRequestId;
}

function appendText(parts: Part[], text: string): Part[] {
  const last = parts.at(-1);
  if (last?.kind === "text") return [...parts.slice(0, -1), { kind: "text", text: last.text + text }];
  return [...parts, { kind: "text", text }];
}

function toApproval(msg: ApprovalRequested): PendingApproval {
  return {
    approvalId: msg.approval_id,
    reason: msg.reason,
    args: msg.args,
    untrusted: msg.untrusted_context,
    canRemember: msg.can_remember,
    expiresInS: msg.expires_in_s,
  };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.kind) {
    case "new_session":
      return { ...initialChatState, paused: state.paused };
    case "load_session":
      return {
        ...initialChatState,
        paused: state.paused,
        sessionId: action.sessionId,
        messages: action.messages.map((m, i) => ({
          id: `h-${i}`,
          role: m.role,
          parts: [{ kind: "text", text: m.text }],
          status: "done",
        })),
      };
    case "local_send":
      return {
        ...state,
        pendingRequestId: action.requestId,
        lastError: null,
        messages: [
          ...state.messages,
          { id: `u-${action.requestId}`, role: "user", parts: [{ kind: "text", text: action.text }], status: "done" },
          { id: `a-${action.requestId}`, role: "assistant", parts: [], status: "streaming", prompt: action.text },
        ],
      };
    case "approval_sent":
      return updateTool(
        state,
        (p) => p.approval?.approvalId === action.approvalId,
        (p) => ({ ...p, approval: undefined, decision: action.approved ? "Approved" : "Denied" }),
      );
    case "server":
      return applyServer(state, action.message);
  }
}

/** Events for turns this tab didn't start (background tasks) belong to the Tasks panel. */
function isOurs(state: ChatState, requestId: string): boolean {
  return state.pendingRequestId === requestId || state.messages.some((m) => m.id === `a-${requestId}`);
}

function applyServer(state: ChatState, msg: ServerMessage): ChatState {
  if ("request_id" in msg && msg.request_id !== null && !isOurs(state, msg.request_id)) return state;
  switch (msg.type) {
    case "control.state":
      return { ...state, paused: msg.paused };
    case "chat.started": {
      if (msg.request_id !== state.pendingRequestId) return state;
      const target = msg.target ?? undefined;
      const messages = target
        ? state.messages.map((m) => (m.id === `u-${msg.request_id}` ? { ...m, target } : m))
        : state.messages;
      return { ...state, sessionId: msg.session_id, messages };
    }
    case "chat.thinking":
      return updateAssistant(state, msg.request_id, (m) => ({ ...m, status: "thinking" }));
    case "chat.delta":
      return updateAssistant(state, msg.request_id, (m) => ({
        ...m,
        parts: appendText(m.parts, msg.text),
        status: "streaming",
      }));
    case "tool.started":
      return updateAssistant(state, msg.request_id, (m) => ({
        ...m,
        status: "streaming",
        parts: [
          ...m.parts,
          { kind: "tool", callId: msg.call_id, tool: msg.tool, summary: msg.summary, risk: msg.risk, status: "running" },
        ],
      }));
    case "approval.request":
      return updateTool(
        state,
        (p) => p.callId === msg.call_id,
        (p) => ({ ...p, status: "waiting", approval: toApproval(msg) }),
      );
    case "approval.resolved":
      return updateTool(
        state,
        (p) => p.approval?.approvalId === msg.approval_id,
        (p) => ({ ...p, approval: undefined, decision: msg.reason }),
      );
    case "tool.finished": {
      const capture =
        msg.capture_id && msg.capture_width && msg.capture_height
          ? { id: msg.capture_id, width: msg.capture_width, height: msg.capture_height }
          : undefined;
      let next = updateTool(
        state,
        (p) => p.callId === msg.call_id,
        (p) => ({
          ...p,
          status: msg.status,
          verified: msg.verified ?? undefined,
          preview: msg.preview,
          durationMs: msg.duration_ms,
          approval: undefined,
          image: msg.image ?? p.image,
          capture: capture ?? p.capture,
        }),
      );
      const box = msg.highlight;
      if (box) {
        // Draw the box on the screenshot it refers to, wherever that is in the chat.
        next = updateTool(
          next,
          (p) => p.capture?.id === box.capture_id,
          (p) => ({ ...p, highlights: [...(p.highlights ?? []), box] }),
        );
      }
      return next;
    }
    case "chat.done": {
      const { model, usage, cost_usd, latency_ms, first_token_ms, stop_reason, route, timings, skill, escalated } = msg;
      const next = updateAssistant(state, msg.request_id, (m) => ({
        ...m,
        status: "done",
        meta: { model, usage, cost_usd, latency_ms, first_token_ms, stop_reason, route, timings, skill, escalated },
      }));
      return { ...next, pendingRequestId: settled(state, msg.request_id) };
    }
    case "chat.cancelled": {
      const next = updateAssistant(state, msg.request_id, (m) => ({ ...m, status: "cancelled" }));
      return { ...next, pendingRequestId: settled(state, msg.request_id) };
    }
    case "chat.error": {
      if (msg.request_id === null) return { ...state, lastError: msg.message };
      const next = updateAssistant(state, msg.request_id, (m) => ({ ...m, status: "error", error: msg.message }));
      return { ...next, pendingRequestId: settled(state, msg.request_id), lastError: msg.message };
    }
    default:
      return state;
  }
}
