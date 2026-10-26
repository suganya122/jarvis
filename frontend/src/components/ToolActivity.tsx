import { useEffect, useId, useRef, useState } from "react";
import type { ToolPart } from "../chatState";
import type { RiskLevel } from "../contracts";
import { Screenshot } from "./Screenshot";

const RISK_LABELS: Record<RiskLevel, string> = {
  read: "Read",
  write: "Changes something",
  destructive: "Destructive",
  external_send: "Sends data out",
  purchase: "Purchase",
};
const RISK_NAMES: Record<RiskLevel, string> = {
  read: "read only",
  write: "changes something",
  destructive: "deletes or overwrites",
  external_send: "external send",
  purchase: "purchase",
};
const MAX_FIELDS = 6;

/** The call's arguments as a few readable fields (To, Subject, Path…); everything else is in Details. */
function fields(args: Record<string, unknown>): [string, string][] {
  return Object.entries(args)
    .filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
    .slice(0, MAX_FIELDS)
    .map(([k, v]) => {
      const label = k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
      const text = String(v);
      return [label, text.length > 140 ? `${text.slice(0, 140)}…` : text];
    });
}

const STATUS_LABELS: Record<ToolPart["status"], string> = {
  running: "Running…",
  waiting: "Waiting for your approval",
  ok: "Done",
  error: "Failed",
  denied: "Not run",
  blocked: "Blocked",
  cancelled: "Cancelled",
};

export interface ApprovalHandlers {
  onDecide: (approvalId: string, approved: boolean, remember: boolean) => void;
}

interface Props extends ApprovalHandlers {
  part: ToolPart;
  /** "Edit first": deny this, and put the request back in the message box. */
  onEdit?: () => void;
}

export function ToolActivity({ part, onDecide, onEdit }: Props) {
  return (
    <div className={`tool tool-${part.status}`}>
      <div className="tool-line">
        {part.risk !== "read" && <span className={`risk risk-${part.risk}`}>{RISK_LABELS[part.risk]}</span>}
        <span className="tool-summary">{part.summary.split("\n", 1)[0]}</span>
        {part.verified && <span className="tag-verified">verified</span>}
        <span className="tool-status">
          {STATUS_LABELS[part.status]}
          {part.durationMs !== undefined && part.status === "ok" && ` · ${Math.round(part.durationMs)} ms`}
        </span>
      </div>
      {part.approval && <ApprovalCard part={part} onDecide={onDecide} onEdit={onEdit} />}
      {part.image && part.capture && (
        <Screenshot
          src={part.image}
          width={part.capture.width}
          height={part.capture.height}
          highlights={part.highlights ?? []}
        />
      )}
      {!part.approval && part.decision && part.status !== "ok" && <p className="tool-note">{part.decision}</p>}
      {part.preview && part.status !== "ok" && <p className="tool-note">{part.preview}</p>}
      {part.preview && part.status === "ok" && (
        <details className="tool-output">
          <summary>Output</summary>
          <pre>{part.preview}</pre>
        </details>
      )}
    </div>
  );
}

function ApprovalCard({ part, onDecide, onEdit }: Props) {
  const approval = part.approval!;
  const [remember, setRemember] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  const highRisk = part.risk !== "read" && part.risk !== "write";

  useEffect(() => {
    // Move focus to the card (not to Approve), so a stray Enter can't approve anything.
    ref.current?.focus();
  }, []);

  return (
    <section
      ref={ref}
      tabIndex={-1}
      className={`approval${highRisk ? " approval-high" : ""}`}
      role="group"
      aria-labelledby={titleId}
    >
      <div className="approval-head">
        <h3 id={titleId} className="approval-title">
          Allow {part.tool}?
        </h3>
        <span className="approval-risk">Risk: {RISK_NAMES[part.risk]}</span>
      </div>
      <p className="approval-summary">{part.summary}</p>
      {fields(approval.args).length > 0 && (
        <dl className="approval-fields">
          {fields(approval.args).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="hint">Asking because {approval.reason}.</p>
      {approval.untrusted && (
        <p className="approval-warn" role="note">
          This chat contains content from files, pages, or command output. Check this request is really what you
          asked for.
        </p>
      )}
      <details>
        <summary>All details</summary>
        <pre>{JSON.stringify(approval.args, null, 2)}</pre>
      </details>
      {approval.canRemember && (
        <label className="radio">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Allow {part.tool} for the rest of this chat
        </label>
      )}
      <div className="row">
        <button type="button" className="primary" onClick={() => onDecide(approval.approvalId, true, remember)}>
          {part.risk === "external_send" ? "Approve and send" : "Approve"}
        </button>
        <button type="button" onClick={() => onDecide(approval.approvalId, false, false)}>
          Deny
        </button>
        {onEdit && (
          <button type="button" onClick={() => (onDecide(approval.approvalId, false, false), onEdit())}>
            Edit first
          </button>
        )}
        <span className="hint">Times out in {Math.round(approval.expiresInS / 60) || 1} min</span>
      </div>
    </section>
  );
}
