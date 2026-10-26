import { useCallback, useEffect, useState } from "react";
import { ApiError, type Api } from "../api";
import { LoadState } from "./LoadState";
import { Page } from "./Page";
import type { AuditEntry } from "../contracts";

function describe(entry: AuditEntry): string {
  const d = entry.detail;
  const parts = Object.entries(d)
    .filter(([, v]) => v !== null && v !== "" && !(typeof v === "object" && Object.keys(v as object).length === 0))
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  const text = parts.join(" · ");
  return text.length > 240 ? `${text.slice(0, 240)}…` : text;
}

export function AuditPage({ api, onClose }: { api: Api; onClose: () => void }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    (beforeId?: number) =>
      api.audit(beforeId).then(
        (page) => {
          setEntries((prev) => (beforeId ? [...prev, ...page] : page));
          setDone(page.length < 100);
          setError(null);
          setLoading(false);
        },
        (err: unknown) => {
          setError(err instanceof ApiError ? err.message : "the app didn't answer");
          setLoading(false);
        },
      ),
    [api],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const again = () => (setLoading(true), void load());
  const refresh = (
    <button type="button" onClick={again}>
      Refresh
    </button>
  );
  return (
    <Page id="audit-title" title="Audit log" onClose={onClose} actions={refresh} wide>
      <p className="hint">Everything the assistant did or was asked to do. This log can't be edited or deleted.</p>
      <LoadState loading={loading} error={error} empty={entries.length === 0} onRetry={again}
                 what="the audit log" emptyText="Nothing has happened yet." />
      {entries.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Who</th>
                <th scope="col">Action</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="nowrap">{new Date(e.created_at * 1000).toLocaleString()}</td>
                  <td>{e.actor}</td>
                  <td className="nowrap">{e.action}</td>
                  <td className="detail">{describe(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!done && entries.length > 0 && (
        <div className="row end">
          <button type="button" onClick={() => void load(entries.at(-1)?.id)}>
            Load older
          </button>
        </div>
      )}
    </Page>
  );
}
