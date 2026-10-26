import { useCallback, useEffect, useState } from "react";
import { LoadState } from "./LoadState";
import { Page } from "./Page";
import { ApiError, type Api } from "../api";
import type { RoutineView, Watcher } from "../contracts";

function when(epoch: number | null): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" });
}

const WATCH_STATUS: Record<Watcher["status"], string> = {
  active: "Watching",
  paused: "Paused",
  triggered: "Happened",
  error: "Can't check",
};

function NotificationsButton() {
  const supported = typeof Notification !== "undefined";
  const [permission, setPermission] = useState(supported ? Notification.permission : "denied");
  if (!supported) return <p className="hint">This browser can't show notifications; they still appear in the app.</p>;
  if (permission === "granted") return <p className="hint">Browser notifications are on.</p>;
  if (permission === "denied") return <p className="hint">Browser notifications are blocked in this browser's settings.</p>;
  return (
    <button type="button" onClick={() => void Notification.requestPermission().then(setPermission)}>
      Also notify me through the browser
    </button>
  );
}

export function AutomationsPage({ api, onClose }: { api: Api; onClose: () => void }) {
  const [routines, setRoutines] = useState<RoutineView[] | null>(null);
  const [watchers, setWatchers] = useState<Watcher[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.routines(), api.watchers()]).then(
      ([r, w]) => {
        setRoutines(r);
        setWatchers(w);
        setLoadError(null);
      },
      (err: unknown) => setLoadError(err instanceof ApiError ? err.message : "the app didn't answer"),
    );
  }, [api]);

  useEffect(load, [load]);

  const act = async (p: Promise<unknown>) => {
    try {
      await p;
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save.");
    }
    load();
  };

  const toggle = (id: string, enabled: boolean) => {
    // Show the change right away; the reload afterwards corrects it if saving failed.
    setRoutines((prev) => prev?.map((v) => (v.routine.routine_id === id ? { ...v, routine: { ...v.routine, enabled } } : v)) ?? null);
    void act(api.setRoutineEnabled(id, enabled));
  };

  return (
    <Page id="auto-title" title="Routines and watchers" onClose={onClose}>
      <p className="hint">
        Create them in chat: “every weekday at 8am, summarize my inbox”, “remind me at 5pm to call mom”, or “tell me
        when example.com is back up”. Watchers check on their own and never use the AI.
      </p>
      <NotificationsButton />
      <LoadState loading={routines === null} error={loadError} empty={false} onRetry={load} what="routines"
                 emptyText="" />
      {error && <p className="error-line">{error}</p>}
      <h3>Routines</h3>
      {routines?.length === 0 && <p className="state state-empty">No routines yet.</p>}
      {routines?.map(({ routine: r, summary }) => (
        <article key={r.routine_id} className="skill" aria-label={r.name}>
          <p className="skill-name">{summary}</p>
          <p className="hint">
            Next: {r.enabled ? when(r.next_run_at) : "off"} · Last: {r.last_run_at ? `${when(r.last_run_at)}, ${r.last_status}` : r.last_status || "never"}
          </p>
          <div className="row">
            <label className="radio">
              <input type="checkbox" checked={r.enabled} onChange={(e) => toggle(r.routine_id, e.target.checked)} />
              On
            </label>
            <button type="button" onClick={() => void act(api.runRoutine(r.routine_id))}>
              Run now
            </button>
            <button
              type="button"
              className="danger-link"
              onClick={() => window.confirm(`Delete “${r.name}”?`) && void act(api.deleteRoutine(r.routine_id))}
            >
              Delete
            </button>
          </div>
        </article>
      ))}
      <h3>Watchers</h3>
      {watchers?.length === 0 && <p className="state state-empty">No watchers yet.</p>}
      {watchers?.map((w) => (
        <article key={w.watcher_id} className="skill" aria-label={w.name}>
          <div className="row">
            <span className={`badge badge-watch-${w.status}`}>{WATCH_STATUS[w.status]}</span>
            <p className="skill-name">{w.name}</p>
          </div>
          <p className="hint">
            Every {w.every_seconds} s · last checked {when(w.last_checked_at)}
            {w.last_detail && ` · ${w.last_detail}`}
          </p>
          <div className="row">
            {w.status === "active" ? (
              <button type="button" onClick={() => void act(api.setWatcherStatus(w.watcher_id, "paused"))}>
                Pause
              </button>
            ) : (
              <button type="button" onClick={() => void act(api.setWatcherStatus(w.watcher_id, "active"))}>
                {w.status === "paused" ? "Resume" : "Watch again"}
              </button>
            )}
            <button
              type="button"
              className="danger-link"
              onClick={() => window.confirm(`Delete “${w.name}”?`) && void act(api.deleteWatcher(w.watcher_id))}
            >
              Delete
            </button>
          </div>
        </article>
      ))}
    </Page>
  );
}
