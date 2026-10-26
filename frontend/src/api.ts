import type {
  AuditEntry,
  AutostartState,
  PluginInfo,
  CostSummary,
  Memory,
  RoutineView,
  TaskInfo,
  Watcher,
  SessionInfo,
  SetupCheck,
  Skill,
  VoiceStatus,
  Status,
  StoredMessage,
  ToolInfo,
} from "./contracts";

const TOKEN_KEY = "assistant.token";

/** Reads `#token=...` from the URL fragment (never sent to the server), then strips it. */
export function takeTokenFromHash(hash: string): string | null {
  const match = /(?:^#|&)token=([A-Za-z0-9_-]{16,})/.exec(hash);
  return match ? match[1] : null;
}

export function loadToken(): string | null {
  const fromHash = takeTokenFromHash(window.location.hash);
  if (fromHash) {
    saveToken(fromHash);
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return fromHash;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Storage blocked: the token lives in memory for this tab only.
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class Api {
  private readonly token: string;

  constructor(token: string) {
    this.token = token;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const data = (await response.json()) as { detail?: unknown; error?: unknown };
        detail = String(data.detail ?? data.error ?? detail);
      } catch {
        // non-JSON error body
      }
      throw new ApiError(response.status, detail);
    }
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }

  status = () => this.request<Status>("GET", "/api/status");
  cost = () => this.request<CostSummary>("GET", "/api/cost");
  sessions = () => this.request<SessionInfo[]>("GET", "/api/sessions");
  messages = (id: string) => this.request<StoredMessage[]>("GET", `/api/sessions/${encodeURIComponent(id)}/messages`);
  deleteSession = (id: string) => this.request<void>("DELETE", `/api/sessions/${encodeURIComponent(id)}`);
  setApiKey = (apiKey: string) => this.request<void>("PUT", "/api/secrets/anthropic", { api_key: apiKey });
  deleteApiKey = () => this.request<void>("DELETE", "/api/secrets/anthropic");
  audit = (beforeId?: number) =>
    this.request<AuditEntry[]>("GET", `/api/audit?limit=100${beforeId ? `&before_id=${beforeId}` : ""}`);
  tools = () => this.request<ToolInfo[]>("GET", "/api/tools");
  setupHands = () => this.request<SetupCheck>("GET", "/api/setup/hands");
  skills = () => this.request<Skill[]>("GET", "/api/skills");
  patchSkill = (id: string, patch: Record<string, unknown>) =>
    this.request<Skill>("PATCH", `/api/skills/${encodeURIComponent(id)}`, patch);
  voice = () => this.request<VoiceStatus>("GET", "/api/voice");
  /** Read text aloud through the assistant's own voice (not the browser's). */
  say = (text: string) => this.request<{ spoken: boolean }>("POST", "/api/voice/say", { text: text.slice(0, 4000) });
  downloadModel = (id: string) => this.request<{ started: boolean }>("POST", `/api/voice/models/${encodeURIComponent(id)}/download`);
  useModel = (id: string) => this.request<{ ok: boolean }>("POST", `/api/voice/models/${encodeURIComponent(id)}/use`);
  deleteModel = (id: string) => this.request<void>("DELETE", `/api/voice/models/${encodeURIComponent(id)}`);
  deleteSkill = (id: string) => this.request<void>("DELETE", `/api/skills/${encodeURIComponent(id)}`);
  getSettings = () => this.request<Record<string, unknown>>("GET", "/api/settings");
  putSettings = (settings: Record<string, unknown>) =>
    this.request<Record<string, unknown>>("PUT", "/api/settings", settings);
  tasks = () => this.request<TaskInfo[]>("GET", "/api/tasks");
  taskAction = (id: string, action: "cancel" | "resume" | "discard") =>
    this.request<void>("POST", `/api/tasks/${encodeURIComponent(id)}/${action}`);
  routines = () => this.request<RoutineView[]>("GET", "/api/routines");
  setRoutineEnabled = (id: string, enabled: boolean) =>
    this.request<RoutineView>("PATCH", `/api/routines/${encodeURIComponent(id)}`, { enabled });
  runRoutine = (id: string) => this.request<void>("POST", `/api/routines/${encodeURIComponent(id)}/run`);
  deleteRoutine = (id: string) => this.request<void>("DELETE", `/api/routines/${encodeURIComponent(id)}`);
  watchers = () => this.request<Watcher[]>("GET", "/api/watchers");
  setWatcherStatus = (id: string, status: "active" | "paused") =>
    this.request<Watcher>("PATCH", `/api/watchers/${encodeURIComponent(id)}`, { status });
  plugins = () => this.request<PluginInfo[]>("GET", "/api/plugins");
  setPluginEnabled = (name: string, enabled: boolean) =>
    this.request<PluginInfo>("PUT", `/api/plugins/${encodeURIComponent(name)}/enabled`, { enabled });
  setPluginOptions = (name: string, options: Record<string, string>) =>
    this.request<PluginInfo>("PUT", `/api/plugins/${encodeURIComponent(name)}/options`, { options });
  setPluginSecret = (name: string, secret: string, value: string) =>
    this.request<void>("PUT", `/api/plugins/${encodeURIComponent(name)}/secrets/${encodeURIComponent(secret)}`, { value });
  deletePluginSecret = (name: string, secret: string) =>
    this.request<void>("DELETE", `/api/plugins/${encodeURIComponent(name)}/secrets/${encodeURIComponent(secret)}`);
  pairPlugin = (name: string) =>
    this.request<{ code: string; expires_in_s: number }>("POST", `/api/plugins/${encodeURIComponent(name)}/pair`);
  unpair = (name: string, userId: string) =>
    this.request<void>("DELETE", `/api/plugins/${encodeURIComponent(name)}/paired/${encodeURIComponent(userId)}`);
  memories = () => this.request<Memory[]>("GET", "/api/memories");
  addMemory = (text: string) => this.request<Memory>("POST", "/api/memories", { text });
  editMemory = (id: string, text: string) =>
    this.request<Memory>("PUT", `/api/memories/${encodeURIComponent(id)}`, { text });
  deleteMemory = (id: string) => this.request<void>("DELETE", `/api/memories/${encodeURIComponent(id)}`);
  autostart = () => this.request<AutostartState>("GET", "/api/autostart");
  setAutostart = (enabled: boolean) => this.request<AutostartState>("PUT", "/api/autostart", { enabled });
  deleteAllData = () => this.request<{ stopping: boolean }>("POST", "/api/data/delete-all", { confirm: "DELETE" });

  /** Downloads everything as one JSON file (the token can't go in a plain link, so fetch + blob). */
  async exportData(): Promise<void> {
    const response = await fetch("/api/export", { headers: { Authorization: `Bearer ${this.token}` } });
    if (!response.ok) throw new ApiError(response.status, response.statusText);
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? "export.json";
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  deleteWatcher = (id: string) => this.request<void>("DELETE", `/api/watchers/${encodeURIComponent(id)}`);
}
