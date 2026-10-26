import { useState } from "react";

export interface SpendingSettings {
  daily_cap_usd: number | null;
  monthly_cap_usd: number | null;
  warn_at: number;
}

export interface BackgroundSettings {
  max_concurrent: number;
  token_budget: number;
  max_steps: number;
  desktop_notifications: boolean;
}

interface Props {
  spending: SpendingSettings;
  background: BackgroundSettings;
  onSave: (patch: { spending: SpendingSettings; background: BackgroundSettings }) => void;
}

function Cap({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="stack">
      {label}
      <span className="row">
        <span aria-hidden="true">$</span>
        <input type="number" min="0" step="0.5" inputMode="decimal" value={value} placeholder="no cap"
               onChange={(e) => onChange(e.target.value)} />
      </span>
    </label>
  );
}

const asCap = (value: string): number | null => (value.trim() === "" ? null : Math.max(0, Number(value)));

/** Remount (key) when saved settings change: the form starts from what's stored. */
export function SpendingSection({ spending, background, onSave }: Props) {
  const [daily, setDaily] = useState(spending.daily_cap_usd?.toString() ?? "");
  const [monthly, setMonthly] = useState(spending.monthly_cap_usd?.toString() ?? "");
  const [budget, setBudget] = useState(background.token_budget.toString());
  const [steps, setSteps] = useState(background.max_steps.toString());
  const [desktop, setDesktop] = useState(background.desktop_notifications);
  const invalid = [daily, monthly].some((v) => v.trim() !== "" && !(Number(v) >= 0)) || !(Number(budget) >= 5000);

  return (
    <section aria-labelledby="spending-title">
      <h3 id="spending-title">Spending</h3>
      <p className="hint">
        Claude is the only paid part. When a cap is reached, the app stops calling Claude until the day or month resets
        (or you raise the cap). Instant commands, skills, reminders and watchers keep working. Leave a box empty for no
        cap. Amounts are estimates from token usage; your Anthropic bill is the final word.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({
            spending: { ...spending, daily_cap_usd: asCap(daily), monthly_cap_usd: asCap(monthly) },
            background: {
              ...background,
              token_budget: Math.round(Number(budget)),
              max_steps: Math.round(Number(steps)),
              desktop_notifications: desktop,
            },
          });
        }}
      >
        <div className="row">
          <Cap label="Daily cap" value={daily} onChange={setDaily} />
          <Cap label="Monthly cap" value={monthly} onChange={setMonthly} />
        </div>
        <h4>Background tasks and routines</h4>
        <div className="row">
          <label className="stack">
            Token budget per task
            <input type="number" min="5000" step="5000" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </label>
          <label className="stack">
            Max steps per task
            <input type="number" min="1" max="100" value={steps} onChange={(e) => setSteps(e.target.value)} />
          </label>
        </div>
        <label className="radio">
          <input type="checkbox" checked={desktop} onChange={(e) => setDesktop(e.target.checked)} />
          Also show notifications on the desktop
        </label>
        <button type="submit" disabled={invalid}>
          Save spending and budgets
        </button>
      </form>
    </section>
  );
}
