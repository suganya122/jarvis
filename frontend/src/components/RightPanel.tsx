import { useCallback } from "react";
import type { Api } from "../api";
import type { UiMessage } from "../chatState";
import type { Capability, CostSummary, ModelTier, PolicyMode } from "../contracts";
import { formatUsd } from "../format";
import { lastAskLine } from "../lastAsk";
import { useLoad } from "../useLoad";
import { LoadState } from "./LoadState";

const SHORT: Record<Capability, string> = {
  "screen.read": "Read screen",
  "input.control": "Control windows",
  "shell.run": "Run shell commands",
  "fs.read": "Read files",
  "fs.write": "Change files",
  "net.fetch": "Open links",
  "send.external": "Send email or messages",
  purchase: "Purchases",
  schedule: "Schedule things",
  memory: "Save memories",
  browser: "Use the browser",
};
/** These always ask, whatever the setting (policy.decide), unless turned off entirely. */
const ALWAYS_ASK = new Set<Capability>(["send.external", "purchase"]);

function stateLabel(cap: Capability, mode: PolicyMode): string {
  if (mode === "deny") return "Denied";
  if (ALWAYS_ASK.has(cap)) return "Always asks";
  return mode === "allow" ? "Allowed" : "Ask";
}

function Spend({ label, spent, cap }: { label: string; spent: number; cap: number | null }) {
  return (
    <div className="spend">
      <div className="row">
        <span>{label}</span>
        <span className="spacer" />
        <span className="num">
          {formatUsd(spent)}
          {cap !== null && <span className="muted"> / {formatUsd(cap)}</span>}
        </span>
      </div>
      {cap !== null && cap > 0 && (
        <meter min={0} max={cap} low={cap * 0.8} high={cap * 0.95} optimum={0} value={Math.min(spent, cap)}
               aria-label={`${label}: ${formatUsd(spent)} of ${formatUsd(cap)}`} />
      )}
    </div>
  );
}

/** Estimated Claude spending against the caps; amber near a cap, red once reached. */
export function CostMeter({ cost }: { cost: CostSummary }) {
  const level = cost.capped ? "capped" : cost.warning ? "warning" : "ok";
  return (
    <section className={`cost cost-${level}`} aria-labelledby="cost-title" title={`${cost.month_tokens.toLocaleString()} tokens this month`}>
      <h3 id="cost-title">Cost</h3>
      <span className="sr-only">
        Today {formatUsd(cost.today_usd)}
        {cost.daily_cap_usd !== null && ` / ${formatUsd(cost.daily_cap_usd)}`}
      </span>
      <Spend label="Today" spent={cost.today_usd} cap={cost.daily_cap_usd} />
      <Spend label="This month" spent={cost.month_usd} cap={cost.monthly_cap_usd} />
      {cost.capped && <p className="cost-note">{cost.capped === "daily" ? "Daily" : "Monthly"} cap reached: Claude is off.</p>}
    </section>
  );
}

interface Props {
  api: Api;
  cost: CostSummary | null;
  messages: UiMessage[];
  tiers: { tier: ModelTier; model: string }[];
}

/** Remount it (key) after settings change to re-read the permissions. */
export function RightPanel({ api, cost, messages, tiers }: Props) {
  const load = useCallback(() => api.getSettings(), [api]);
  const settings = useLoad(load);
  const policies = (settings.data?.tools as { policies?: Record<Capability, PolicyMode> } | undefined)?.policies;
  return (
    <>
      {cost ? <CostMeter cost={cost} /> : <p className="state">Loading costs…</p>}
      <p className="hint">{lastAskLine(messages, tiers)}</p>
      <section aria-labelledby="perm-title">
        <h3 id="perm-title">Permissions</h3>
        <LoadState {...settings} empty={!policies} onRetry={settings.reload} what="permissions" emptyText="" />
        {policies && (
          <ul className="perm-list">
            {(Object.keys(SHORT) as Capability[]).map((cap) => {
              const label = stateLabel(cap, policies[cap] ?? "ask");
              return (
                <li key={cap}>
                  <span>{SHORT[cap]}</span>
                  <span className={`perm-state perm-${label.toLowerCase().replace(" ", "-")}`}>{label}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <section aria-labelledby="voice-note-title">
        <h3 id="voice-note-title">Voice</h3>
        <p className="hint">Local speech model. Audio never leaves this computer.</p>
      </section>
    </>
  );
}
