import { useEffect, useState } from "react";
import { ApiError, type Api } from "../api";
import type { AutostartState } from "../contracts";

interface Props {
  api: Api;
  appName: string;
  /** Called once the daemon is deleting everything and shutting down. */
  onDeleted: () => void;
}

export function DataSection({ api, appName, onDeleted }: Props) {
  const [autostart, setAutostart] = useState<AutostartState | null>(null);
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.autostart().then(setAutostart, () => setAutostart(null));
  }, [api]);

  const report = (err: unknown) => setMessage(err instanceof ApiError ? err.message : "Something went wrong.");

  const toggle = async (enabled: boolean) => {
    setAutostart({ supported: true, enabled }); // show it right away; corrected below if it fails
    try {
      setAutostart(await api.setAutostart(enabled));
      setMessage(enabled ? `${appName} will start when you log in.` : "Start at login is off.");
    } catch (err) {
      setAutostart({ supported: true, enabled: !enabled });
      report(err);
    }
  };

  const exportAll = async () => {
    try {
      await api.exportData();
      setMessage("Export downloaded. It has your chats, memories, skills and logs (no API key).");
    } catch (err) {
      report(err);
    }
  };

  const deleteAll = async () => {
    try {
      await api.deleteAllData();
      onDeleted();
    } catch (err) {
      report(err);
    }
  };

  return (
    <section aria-labelledby="data-title">
      <h3 id="data-title">Your data</h3>
      {autostart?.supported && (
        <label className="radio">
          <input type="checkbox" checked={autostart.enabled} onChange={(e) => void toggle(e.target.checked)} />
          Start {appName} when I log in
        </label>
      )}
      <div className="row">
        <button type="button" onClick={() => void exportAll()}>
          Export all data
        </button>
      </div>
      <details className="danger-zone">
        <summary>Delete all data…</summary>
        <p className="hint">
          Deletes chats, memories, skills, routines, the activity log, downloaded voice models, your API key from the
          keychain, and the start-at-login entry. Then {appName} stops. This can't be undone; export first if you want a
          copy.
        </p>
        <label className="stack">
          Type DELETE to confirm
          <input value={confirm} autoComplete="off" onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <button type="button" className="stop" disabled={confirm !== "DELETE"} onClick={() => void deleteAll()}>
          Delete everything and stop
        </button>
      </details>
      {message && (
        <p role="status" className="hint">
          {message}
        </p>
      )}
    </section>
  );
}
