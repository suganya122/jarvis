import { useCallback, useEffect, useReducer, useRef, useState, type ReactElement } from "react";
import { Api, ApiError, clearToken, loadToken, saveToken } from "./api";
import { chatReducer, initialChatState, messageText } from "./chatState";
import type {
  CostSummary,
  DownloadProgress,
  ModelTier,
  ServerMessage,
  SessionInfo,
  Status,
  VoiceMode,
  VoiceTiming,
} from "./contracts";
import { useChatSocket } from "./useChatSocket";
import { ChatView } from "./components/ChatView";
import { Composer, type Attach } from "./components/Composer";
import { SessionList } from "./components/SessionList";
import { VoiceSection, type VoiceSettings } from "./components/VoiceSection";
import { stopSpeaking } from "./voice/speech";
import { useVoice, type VoiceFinalInfo } from "./voice/useVoice";
import { TokenGate } from "./components/TokenGate";
import { SettingsPage } from "./components/SettingsPage";
import { AuditPage } from "./components/AuditPage";
import { SkillsPage } from "./components/SkillsPage";
import { TasksPage } from "./components/TasksPage";
import { AutomationsPage } from "./components/AutomationsPage";
import { MemoryPage } from "./components/MemoryPage";
import { Page } from "./components/Page";
import { RightPanel } from "./components/RightPanel";
import { Sidebar, type View } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { Notices } from "./components/Notices";
import { Banners } from "./components/Banners";
import { liveCount, useTasks } from "./tasksState";
import { useHotkeys } from "./useHotkeys";
import { VoiceHome } from "./components/VoiceHome";
import { readTyping, saveTyping } from "./typingBox";

export default function App() {
  const [token, setToken] = useState<string | null>(() => loadToken());
  if (!token) {
    return (
      <TokenGate
        onSubmit={(t) => {
          saveToken(t);
          setToken(t);
        }}
      />
    );
  }
  return (
    <Main
      token={token}
      onUnauthorized={() => {
        clearToken();
        setToken(null);
      }}
    />
  );
}

function newRequestId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function Main({ token, onUnauthorized }: { token: string; onUnauthorized: () => void }) {
  const [api] = useState(() => new Api(token));
  const [chat, dispatch] = useReducer(chatReducer, initialChatState);
  const [status, setStatus] = useState<Status | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [tier, setTier] = useState<ModelTier>("cheap");
  const [appName, setAppName] = useState("Assistant");
  const [typing, setTyping] = useState(readTyping);
  const [view, setView] = useState<View>(() => (typing ? "chat" : "home")); // typing on: open where you type
  const [settingsTick, setSettingsTick] = useState(0);
  const [deleted, setDeleted] = useState(false);
  const [pluginTick, setPluginTick] = useState(0);
  const tasks = useTasks(api);
  const { handle: handleTask, reload: reloadTasks } = tasks;
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings | null>(null);
  const [downloads, setDownloads] = useState<Record<string, DownloadProgress>>({});
  const [insert, setInsert] = useState<{ text: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const voiceHandle = useRef<(msg: ServerMessage) => void>(() => {});
  const voiceRequests = useRef(new Set<string>());
  const dictationModes = useRef(new Map<string, VoiceMode>());
  const voiceRef = useRef<ReturnType<typeof useVoice> | null>(null);

  const guard = useCallback(
    async <T,>(p: Promise<T>): Promise<T | null> => {
      try {
        return await p;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
        return null;
      }
    },
    [onUnauthorized],
  );

  const refresh = useCallback(async () => {
    const [s, c, list] = await Promise.all([guard(api.status()), guard(api.cost()), guard(api.sessions())]);
    if (s) setStatus(s);
    if (c) setCost(c);
    if (list) setSessions(list);
  }, [api, guard]);

  useEffect(() => {
    void (async () => {
      const s = await guard(api.status());
      if (s) setTier(s.default_tier);
      const settings = await guard(api.getSettings());
      if (settings?.voice) setVoiceSettings(settings.voice as VoiceSettings);
      await refresh();
    })();
  }, [api, guard, refresh]);

  const saveVoice = useCallback(
    async (voice: VoiceSettings) => {
      setVoiceSettings(voice); // show the change right away; the saved copy below has the final word
      const current = await guard(api.getSettings());
      if (!current) return;
      const saved = await guard(api.putSettings({ ...current, voice }));
      if (saved?.voice) setVoiceSettings(saved.voice as VoiceSettings);
    },
    [api, guard],
  );

  const onMessage = useCallback(
    (msg: ServerMessage) => {
      if (msg.type === "hello") {
        setAppName(msg.app_name);
        document.title = msg.app_name;
        reloadTasks(); // reconnected: the daemon may have restarted (interrupted tasks)
        return;
      }
      if (msg.type === "plugin.status") {
        setPluginTick((n) => n + 1);
        return;
      }
      handleTask(msg);
      if (msg.type === "task.update" || msg.type === "notify") return;
      voiceHandle.current(msg);
      if (msg.type === "download.progress") {
        setDownloads((prev) => ({ ...prev, [msg.model_id]: msg }));
        return;
      }
      if (msg.type === "dictation.result") {
        const mode = dictationModes.current.get(msg.stream_id);
        if (mode === "dictate_composer") setInsert({ text: msg.text });
        else setToast(msg.inserted ? `Typed your dictation into the app.${msg.note ? ` ${msg.note}` : ""}` : msg.note);
        return;
      }
      if (msg.type === "voice.command") {
        const voice = voiceRef.current;
        if (msg.action === "stop") {
          voice?.stop(true);
          stopSpeaking();
        } else voice?.toggle(msg.action === "toggle_chat" ? "chat" : "dictate_app");
        return;
      }
      dispatch({ kind: "server", message: msg });
      if (msg.type === "chat.done" || msg.type === "chat.cancelled" || msg.type === "chat.error") void refresh();
    },
    [refresh, handleTask, reloadTasks],
  );
  const { state: connection, send } = useChatSocket(token, onMessage);

  const submit = (text: string, attach: Attach = "none", forceModel = false, voice: VoiceTiming | null = null) => {
    const requestId = newRequestId();
    const ok = send({
      type: "chat.send",
      request_id: requestId,
      session_id: chat.sessionId,
      text,
      tier,
      force_model: forceModel,
      attach,
      voice,
    });
    if (ok) dispatch({ kind: "local_send", requestId, text, tier });
    if (ok && voice) voiceRequests.current.add(`a-${requestId}`);
    return ok;
  };

  // Background tasks start in their own chat, so they never mix into this one.
  const submitBackground = (text: string) => {
    const ok = send({ type: "chat.send", request_id: newRequestId(), session_id: null, text, tier, background: true });
    if (ok) setToast("Started in the background. Follow it in Background agents.");
    return ok;
  };

  const onVoiceFinal = (info: VoiceFinalInfo) => {
    if (!info.text) {
      setToast("Didn't catch that. Try again a little closer to the microphone.");
      return;
    }
    if (info.mode === "chat") {
      submit(info.text, "none", false, {
        stream_id: info.streamId,
        release_to_final_ms: info.releaseToFinalMs,
        hotkey_to_first_partial_ms: info.hotkeyToFirstPartialMs,
      });
    } else {
      dictationModes.current.set(info.streamId, info.mode);
    }
  };

  const voice = useVoice({
    send,
    sessionId: chat.sessionId,
    onFinal: onVoiceFinal,
    autoSend: voiceSettings?.auto_send_silence ?? true,
  });
  useEffect(() => {
    document.title = voice.mic === "recording" ? `● Listening — ${appName}` : appName;
  }, [voice.mic, appName]);
  useEffect(() => {
    voiceHandle.current = voice.handle;
    voiceRef.current = voice;
  });


  const cancel = () => {
    if (chat.pendingRequestId) send({ type: "chat.cancel", request_id: chat.pendingRequestId });
  };

  const decide = (approvalId: string, approved: boolean, remember: boolean) => {
    if (send({ type: "approval.respond", approval_id: approvalId, approved, remember })) {
      dispatch({ kind: "approval_sent", approvalId, approved });
    }
  };

  const stopAll = useCallback(() => {
    voiceRef.current?.stop(true);
    stopSpeaking();
    return send({ type: "control.stop_all" });
  }, [send]);

  useHotkeys(voiceRef, stopAll, typing);

  const openSession = async (id: string) => {
    const messages = await guard(api.messages(id));
    if (messages) dispatch({ kind: "load_session", sessionId: id, messages });
  };

  const deleteSession = async (id: string) => {
    await guard(api.deleteSession(id));
    if (chat.sessionId === id) dispatch({ kind: "new_session" });
    await refresh();
  };

  if (deleted) {
    return (
      <main className="gate">
        <h1>All data deleted</h1>
        <p>
          {appName} deleted your chats, memories, skills, settings, logs and API key, and has stopped. You can close
          this tab. Starting it again begins fresh.
        </p>
      </main>
    );
  }

  const toChat = () => setView(typing ? "chat" : "home");
  const pages: Partial<Record<View, ReactElement>> = {
    skills: <SkillsPage api={api} onClose={toChat} />,
    tasks: (
      <TasksPage
        tasks={tasks.state.tasks}
        approvals={tasks.state.approvals}
        onClose={toChat}
        onControl={(taskId, action) => send({ type: "task.control", task_id: taskId, action })}
        onDecide={(approvalId, approved) => send({ type: "approval.respond", approval_id: approvalId, approved, remember: false })}
        onOpenChat={(id) => (setView("chat"), void openSession(id))}
      />
    ),
    automations: <AutomationsPage api={api} onClose={toChat} />,
    memory: <MemoryPage api={api} onClose={toChat} />,
    audit: <AuditPage api={api} onClose={toChat} />,
    settings: (
      <SettingsPage
        voiceSection={voiceSettings && (
          <VoiceSection api={api} voice={voiceSettings} downloads={downloads} mic={voice.mic}
                        onSave={(v) => void saveVoice(v)} onReleaseMic={voice.release}
                        typing={typing} onTyping={(on) => (saveTyping(on), setTyping(on))} />
        )}
        api={api}
        status={status}
        onClose={toChat}
        onChanged={() => (setSettingsTick((n) => n + 1), void refresh())}
        appName={appName}
        pluginTick={pluginTick}
        onDeleted={() => (clearToken(), setDeleted(true))}
      />
    ),
    overview: (
      <Page id="overview-title" title="Overview" onClose={toChat}>
        <RightPanel key={settingsTick} api={api} cost={cost} messages={chat.messages} tiers={status?.tiers ?? []} />
      </Page>
    ),
  };

  return (
    <div className="app">
      <Sidebar
        appName={appName}
        view={view}
        onView={setView}
        liveTasks={liveCount(tasks.state)}
        chats={
          <SessionList
            sessions={sessions}
            activeId={chat.sessionId}
            onOpen={(id) => (setView("chat"), void openSession(id))}
            onDelete={(id) => void deleteSession(id)}
            onNew={typing ? () => (setView("chat"), dispatch({ kind: "new_session" })) : undefined}
          />
        }
      />
      <TopBar
        connection={connection}
        tier={tier}
        tiers={status?.tiers ?? []}
        onTier={setTier}
        paused={chat.paused}
        onStopAll={stopAll}
        onTogglePause={() => send({ type: "control.pause", paused: !chat.paused })}
      />
      <main className="main">
        <div className="chat" role="region" aria-label="Chat" hidden={view !== "chat"}>
          <Banners status={status} cost={cost} paused={chat.paused} connection={connection}
                   onOpenSettings={() => setView("settings")} />
          <ChatView
            messages={chat.messages}
            onDecide={decide}
            busy={chat.pendingRequestId !== null}
            onAskModel={(text) => submit(text, "none", true)}
            onSpeak={(m) => void api.say(messageText(m)).catch(() => setToast("Couldn't read it aloud: voice output isn't running."))}
            onEdit={(text) => setInsert({ text })}
          />
          {chat.lastError && (
            <p className="error-line" role="alert">
              {chat.lastError}
            </p>
          )}
          {(voice.error || toast) && (
            <p className="toast" role="status">
              {voice.error ?? toast}{" "}
              <button type="button" className="link" onClick={() => (voice.clearError(), setToast(null))}>
                Dismiss
              </button>
            </p>
          )}
          {typing && (
          <Composer
            appName={appName}
            busy={chat.pendingRequestId !== null}
            disabled={connection !== "open" || chat.paused}
            onSubmit={(text, attach, background) => (background ? submitBackground(text) : submit(text, attach))}
            onCancel={cancel}
            insert={insert}
            voice={{
              mic: voice.mic,
              listening: voice.mic === "recording" ? voice.mode : null,
              partial: voice.partial,
              onTalk: () => voice.toggle("chat"),
              onDictate: () => voice.toggle("dictate_composer"),
            }}
          />
          )}
        </div>
        {view === "home" && (
          <VoiceHome
            api={api}
            wakeWord={voiceSettings?.wake_word ?? ""}
            sessions={sessions}
            onOpenHistory={(id) => (setView("chat"), void openSession(id))}
            onOpenSettings={() => setView("settings")}
            onActivity={() => void refresh()}
            banners={<Banners status={status} cost={cost} paused={chat.paused} connection={connection}
                              onOpenSettings={() => setView("settings")} />}
          />
        )}
        {view !== "chat" && view !== "home" && pages[view]}
      </main>
      <aside className="right-panel" aria-label="Status">
        <RightPanel key={settingsTick} api={api} cost={cost} messages={chat.messages} tiers={status?.tiers ?? []} />
      </aside>
      <Notices notices={tasks.state.notices} onDismiss={tasks.dismiss} onOpenTask={() => setView("tasks")} />
    </div>
  );
}
