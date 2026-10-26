import type { ModelTier } from "../contracts";
import type { ConnectionState } from "../useChatSocket";
import { ThemeToggle } from "./ThemeToggle";

const TIER_LABELS: Record<ModelTier, string> = { cheap: "Cheap", balanced: "Balanced", max: "Max" };

interface Props {
  connection: ConnectionState;
  tier: ModelTier;
  tiers: { tier: ModelTier; model: string }[];
  onTier: (tier: ModelTier) => void;
  paused: boolean;
  onStopAll: () => void;
  onTogglePause: () => void;
}

export function TopBar({ connection, tier, tiers, onTier, paused, onStopAll, onTogglePause }: Props) {
  return (
    <header className="topbar">
      <span className={`chip dot dot-${connection}`} role="status" aria-label={`Connection: ${connection}`}>
        {connection === "open" ? "Online" : connection === "connecting" ? "Connecting" : "Offline"}
      </span>
      <span className="chip" title="Everything stays on this computer; only your Claude requests leave it.">
        Local only. Not shared.
      </span>
      <label className="sr-only" htmlFor="tier">
        Model
      </label>
      <select id="tier" className="chip" value={tier} title={tiers.find((x) => x.tier === tier)?.model}
              onChange={(e) => onTier(e.target.value as ModelTier)}>
        {(Object.keys(TIER_LABELS) as ModelTier[]).map((t) => (
          <option key={t} value={t} title={tiers.find((x) => x.tier === t)?.model}>
            {TIER_LABELS[t]}
          </option>
        ))}
      </select>
      <span
        className="chip chip-mint"
        title="Deleting, sending and buying always ask; pages and files can't give the assistant permissions."
      >
        Safe mode on
      </span>
      <span className="spacer" />
      <ThemeToggle />
      <button type="button" aria-pressed={paused} onClick={onTogglePause}>
        {paused ? "Resume everything" : "Pause everything"}
      </button>
      <button type="button" className="stop" onClick={onStopAll} title="Stop everything (Ctrl+Shift+K)">
        Stop all
      </button>
    </header>
  );
}
