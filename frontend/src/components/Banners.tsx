import type { CostSummary, Status } from "../contracts";
import type { ConnectionState } from "../useChatSocket";

interface Props {
  status: Status | null;
  cost: CostSummary | null;
  paused: boolean;
  connection: ConnectionState;
  onOpenSettings: () => void;
}

/** One-line notices above the chat: missing key, spending cap, pause, tools down, offline. */
export function Banners({ status, cost, paused, connection, onOpenSettings }: Props) {
  const needsKey = status?.provider.provider === "anthropic" && status.api_key_source === "none";
  return (
    <>
      {needsKey && (
        <div className="banner" role="status">
          No Anthropic API key yet.{" "}
          <button type="button" className="link" onClick={onOpenSettings}>
            Add one in Settings
          </button>
          .
        </div>
      )}
      {cost?.capped && (
        <div className="banner warn" role="status">
          {cost.capped === "daily" ? "Today's" : "This month's"} spending cap is reached, so Claude is off until it
          resets. Instant commands, skills, reminders and watchers still work.{" "}
          <button type="button" className="link" onClick={onOpenSettings}>
            Change the cap
          </button>
        </div>
      )}
      {paused && (
        <div className="banner warn" role="status">
          Paused. Nothing will run until you press Resume.
        </div>
      )}
      {status && (!status.os_tools || !status.hands) && connection === "open" && (
        <div className="banner" role="status">
          {!status.os_tools ? "File and app tools" : "Window control"} are unavailable right now; chat still works.
        </div>
      )}
      {connection !== "open" && (
        <div className="banner warn" role="status">
          {connection === "connecting" ? "Connecting to the local daemon…" : "Daemon offline. Reconnecting…"}
        </div>
      )}
    </>
  );
}
