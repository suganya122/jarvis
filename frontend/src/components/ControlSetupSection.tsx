import { useState } from "react";
import type { Api } from "../api";
import type { SetupCheck } from "../contracts";

export function ControlSetupSection({ api }: { api: Api }) {
  const [check, setCheck] = useState<SetupCheck | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setCheck(await api.setupHands());
    } catch {
      setCheck({ ok: false, report: "Could not run the check.", details: {} });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="control-title">
      <h3 id="control-title">Computer control</h3>
      <p className="hint">
        The assistant sees other apps' windows and controls through your system's accessibility features, the same
        ones screen readers use. Password fields are never read, and apps like password managers are never touched.
      </p>
      <div className="row">
        <button type="button" onClick={() => void run()} disabled={busy}>
          {busy ? "Checking…" : "Check setup"}
        </button>
        {check && <span className={check.ok ? "ok-line" : "error-line"}>{check.ok ? "Ready" : "Needs attention"}</span>}
      </div>
      {check && (
        <pre className="report" aria-live="polite">
          {check.report}
        </pre>
      )}
    </section>
  );
}
