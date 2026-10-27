// HTTP API shapes (mirrors of backend/core/api.py, automation_api.py and contracts/*.py).

import type { Capability, ChannelState, ModelTier, RiskLevel } from "./contracts";

export interface ProviderStatus {
  provider: string;
  ready: boolean;
  detail: string;
}

export interface Status {
  provider: ProviderStatus;
  os_tools: boolean;
  hands: boolean;
  paused: boolean;
  api_key_source: "keychain" | "environment" | "none";
  keychain_error: string | null;
  default_tier: ModelTier;
  tiers: { tier: ModelTier; model: string }[];
}

export interface SessionInfo {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
}

export interface StoredMessage {
  role: "user" | "assistant";
  text: string;
  trace_id: string;
  created_at: number;
}

/** Spending so far and the caps (backend core/spend.py SpendStatus). */
export interface CostSummary {
  today_usd: number;
  month_usd: number;
  today_tokens: number;
  month_tokens: number;
  daily_cap_usd: number | null;
  monthly_cap_usd: number | null;
  /** Which cap is reached: Claude calls are refused until it resets or is raised. */
  capped: "daily" | "monthly" | null;
  warning: boolean;
}

export interface Memory {
  memory_id: string;
  text: string;
  source: "user" | "assistant";
  created_at: number;
  updated_at: number;
}

/** One thing Jarvis learned: "prefers contact via: WhatsApp", with the words it came from. */
export interface KnowledgeFact {
  id: string;
  text: string;
  predicate: string;
  /** The other person or place, when the fact links two. */
  object_id: string | null;
  since: number;
  /** What you said that it was learned from. */
  quote: string;
  /** False once a newer fact replaced it (kept as history). */
  current: boolean;
}

export interface KnowledgeEntity {
  id: string;
  name: string;
  type: string;
  aliases: string[];
  /** How they are connected to you ("sibling", "parent"…), or "". */
  relation: string;
  mentions: number;
  last_seen_at: number;
  facts: KnowledgeFact[];
}

export interface Knowledge {
  you: KnowledgeFact[];
  entities: KnowledgeEntity[];
  counts: { entities: number; facts: number };
}

export interface PluginField {
  name: string;
  label: string;
  help: string;
  advanced: boolean;
}

export interface PluginManifest {
  name: string;
  display_name: string;
  version: string;
  description: string;
  contract_version: string;
  provides: "channel"[];
  capabilities: Capability[];
  entrypoint: string;
  options: PluginField[];
  secrets: PluginField[];
}

export interface PairedAccount {
  user_id: string;
  name: string;
  paired_at: number;
}

export interface PluginInfo {
  manifest: PluginManifest;
  first_party: boolean;
  enabled: boolean;
  state: ChannelState;
  detail: string;
  options: Record<string, string>;
  secrets_set: Record<string, boolean>;
  paired: PairedAccount[];
}

export interface AutostartState {
  supported: boolean;
  enabled: boolean;
}

export interface AuditEntry {
  id: number;
  created_at: number;
  trace_id: string | null;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
}

export interface ToolInfo {
  name: string;
  description: string;
  risk: RiskLevel;
  capabilities: Capability[];
  source: string;
}

export interface SetupCheck {
  ok: boolean;
  report: string;
  details: Record<string, unknown>;
}

export interface SkillStep {
  tool: string;
  args_template: Record<string, unknown>;
  checks: ("ok" | "verified")[];
}

export interface Skill {
  skill_id: string;
  name: string;
  triggers: string[];
  params: { name: string; kind: "text" | "number"; example: string }[];
  steps: SkillStep[];
  status: "candidate" | "active" | "demoted";
  enabled: boolean;
  verified_successes: number;
  failures: number;
  consecutive_failures: number;
  last_used_at: number | null;
}

export interface VoiceModelInfo {
  id: string;
  title: string;
  size: number;
  license: string;
  tier: string;
  installed: boolean;
  downloading: boolean;
  active: boolean;
  recommended: boolean;
}

export interface VoiceStatus {
  engine_running: boolean;
  models: VoiceModelInfo[];
  shortcut_commands: Record<string, string>;
  overlay: { enabled: boolean; available: boolean | null; detail: string };
  hands_free: HandsFreeStatus;
}

/** The always-listening voice loop (backend core/hands_free.py). */
export interface HandsFreeStatus {
  state: "off" | "idle" | "listening" | "thinking" | "speaking" | "approval";
  detail: string;
  voice: string;
  /** Run this in a terminal to install the natural voice. */
  natural_voice_command: string;
}

// ---- background tasks, routines, watchers (backend/contracts/automation.py) ----

export type TaskStatus = "queued" | "running" | "waiting_approval" | "done" | "failed" | "cancelled" | "interrupted";

export interface TaskInfo {
  task_id: string;
  session_id: string | null;
  prompt: string;
  /** "user", "routine:<id>" or "watcher:<id>". */
  origin: string;
  status: TaskStatus;
  step: number;
  preview: string;
  current_tool: string;
  tokens: number;
  cost_usd: number;
  error: string;
  created_at: number;
  finished_at: number | null;
}

export interface Schedule {
  kind: "once" | "daily" | "weekdays" | "weekly" | "interval";
  time: string | null;
  days: number[];
  every_minutes: number | null;
  at: number | null;
}

export interface Routine {
  routine_id: string;
  name: string;
  prompt: string;
  schedule: Schedule;
  action: "run" | "notify";
  enabled: boolean;
  created_at: number;
  next_run_at: number | null;
  last_run_at: number | null;
  last_status: string;
}

export interface RoutineView {
  routine: Routine;
  summary: string;
}

export interface Watcher {
  watcher_id: string;
  name: string;
  kind: "file" | "url" | "window";
  target: string;
  condition: "changes" | "exists" | "gone" | "contains" | "up" | "down";
  text: string;
  every_seconds: number;
  action: "notify" | "run";
  prompt: string;
  once: boolean;
  status: "active" | "paused" | "triggered" | "error";
  created_at: number;
  last_checked_at: number | null;
  last_detail: string;
}
