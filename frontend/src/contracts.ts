// Hand-written mirror of backend/contracts/ws.py and the HTTP API shapes.
// backend/tests/test_contracts.py fails if the message `type` literals drift.
// HTTP shapes live in contractsHttp.ts and are re-exported from here.

import type { TaskInfo } from "./contractsHttp";

export type * from "./contractsHttp";

export type ModelTier = "cheap" | "balanced" | "max";
export type RiskLevel = "read" | "write" | "destructive" | "external_send" | "purchase";
export type Capability =
  | "screen.read"
  | "input.control"
  | "shell.run"
  | "fs.read"
  | "fs.write"
  | "net.fetch"
  | "send.external"
  | "purchase"
  | "schedule"
  | "memory"
  | "browser";
export type PolicyMode = "allow" | "ask" | "deny";
export type ToolStatus = "ok" | "error" | "denied" | "blocked" | "cancelled";

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
}

// ---- client -> server ----

export interface ChatSend {
  type: "chat.send";
  request_id: string;
  session_id?: string | null;
  text: string;
  tier?: ModelTier | null;
  force_model?: boolean;
  attach?: "none" | "screen" | "select";
  voice?: VoiceTiming | null;
  /** Run as a background task: detached from this tab, with its own budget. */
  background?: boolean;
}

export interface VoiceTiming {
  stream_id: string;
  hotkey_to_first_partial_ms?: number | null;
  release_to_final_ms?: number | null;
}

export type VoiceMode = "chat" | "dictate_composer" | "dictate_app";

export interface VoiceStart {
  type: "voice.start";
  stream_id: string;
  sample_rate: number;
  mode: VoiceMode;
  session_id?: string | null;
}

export interface VoiceAudio {
  type: "voice.audio";
  stream_id: string;
  pcm_b64: string;
}

export interface VoiceStop {
  type: "voice.stop";
  stream_id: string;
  cancel?: boolean;
}

export interface ChatCancel {
  type: "chat.cancel";
  request_id: string;
}

export interface Ping {
  type: "ping";
}

export interface ApprovalRespond {
  type: "approval.respond";
  approval_id: string;
  approved: boolean;
  remember: boolean;
}

export interface StopAll {
  type: "control.stop_all";
}

export interface SetPaused {
  type: "control.pause";
  paused: boolean;
}

export interface TaskControl {
  type: "task.control";
  task_id: string;
  action: "cancel" | "resume" | "discard";
}

export type ClientMessage =
  | ChatSend
  | ChatCancel
  | Ping
  | ApprovalRespond
  | StopAll
  | SetPaused
  | VoiceStart
  | VoiceAudio
  | VoiceStop
  | TaskControl;

// ---- server -> client ----

export interface Hello {
  type: "hello";
  app_name: string;
  app_version: string;
  contract_version: string;
}

export interface ChatStarted {
  type: "chat.started";
  request_id: string;
  trace_id: string;
  session_id: string;
  tier: ModelTier;
  /** Voice turns: what "this"/"here" refers to, e.g. "Screen 2 · Chrome · Inbox". */
  target?: string | null;
}

export interface ChatDelta {
  type: "chat.delta";
  request_id: string;
  text: string;
}

export interface ChatThinking {
  type: "chat.thinking";
  request_id: string;
}

export interface ChatDone {
  type: "chat.done";
  request_id: string;
  model: string;
  stop_reason: string;
  usage: Usage;
  cost_usd: number;
  latency_ms: number;
  first_token_ms: number | null;
  route: "instant" | "skill" | "model";
  timings: Record<string, number>;
  skill?: SkillNote | null;
  /** Why the turn moved from Cheap to Balanced part-way, if it did. */
  escalated?: string | null;
}

export interface SkillNote {
  event: "learned" | "progress" | "promoted" | "replayed" | "failed" | "demoted";
  skill_id: string;
  name: string;
  successes: number;
  needed: number;
}

export interface ChatCancelled {
  type: "chat.cancelled";
  request_id: string;
}

export interface ChatError {
  type: "chat.error";
  request_id: string | null;
  code: string;
  message: string;
}

export interface Pong {
  type: "pong";
}

export interface ToolStarted {
  type: "tool.started";
  request_id: string;
  call_id: string;
  tool: string;
  summary: string;
  risk: RiskLevel;
}

export interface ToolFinished {
  type: "tool.finished";
  request_id: string;
  call_id: string;
  status: ToolStatus;
  preview: string;
  duration_ms: number;
  image?: string | null;
  capture_id?: string | null;
  capture_width?: number | null;
  capture_height?: number | null;
  highlight?: HighlightBox | null;
  /** The tool checked that its action really happened. */
  verified?: boolean | null;
}

export interface HighlightBox {
  capture_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
}

export interface ApprovalRequested {
  type: "approval.request";
  approval_id: string;
  request_id: string;
  call_id: string;
  tool: string;
  risk: RiskLevel;
  summary: string;
  reason: string;
  args: Record<string, unknown>;
  untrusted_context: boolean;
  can_remember: boolean;
  expires_in_s: number;
}

export interface ApprovalResolved {
  type: "approval.resolved";
  approval_id: string;
  approved: boolean;
  reason: string;
}

export interface VoicePartial {
  type: "voice.partial";
  stream_id: string;
  text: string;
}

export interface VoiceFinal {
  type: "voice.final";
  stream_id: string;
  text: string;
  mode: VoiceMode;
  release_to_final_ms: number;
}

export interface VoiceError {
  type: "voice.error";
  stream_id: string | null;
  message: string;
}

export interface VoiceCommand {
  type: "voice.command";
  action: "toggle_chat" | "toggle_dictation" | "stop";
}

export interface DictationResult {
  type: "dictation.result";
  stream_id: string;
  text: string;
  raw: string;
  method: "llm" | "local" | "raw";
  inserted: boolean;
  note: string;
}

export interface DownloadProgress {
  type: "download.progress";
  model_id: string;
  done: number;
  total: number;
  state: "downloading" | "verifying" | "installing" | "done" | "error";
  error: string;
}

export interface ControlState {
  type: "control.state";
  paused: boolean;
}

export interface TaskUpdate {
  type: "task.update";
  task: TaskInfo;
}

export interface Notify {
  type: "notify";
  notify_id: string;
  title: string;
  body: string;
  kind: "reminder" | "watcher" | "task" | "routine" | "voice";
  task_id?: string | null;
}

export type ChannelState = "starting" | "connected" | "error" | "stopped";

export interface PluginStatus {
  type: "plugin.status";
  name: string;
  state: ChannelState;
  detail: string;
}

export type ServerMessage =
  | Hello
  | ChatStarted
  | ChatDelta
  | ChatThinking
  | ChatDone
  | ChatCancelled
  | ChatError
  | Pong
  | ToolStarted
  | ToolFinished
  | ApprovalRequested
  | ApprovalResolved
  | VoicePartial
  | VoiceFinal
  | VoiceError
  | VoiceCommand
  | DictationResult
  | DownloadProgress
  | ControlState
  | TaskUpdate
  | Notify
  | PluginStatus;

export const WS_SUBPROTOCOL = "assistant.v1";
export const WS_TOKEN_PREFIX = "token.";
export const MAX_CHAT_TEXT = 32_000;
