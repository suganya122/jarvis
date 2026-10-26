import { useState } from "react";
import type { Capability, PolicyMode } from "../contracts";
import { CAPABILITY_LABELS } from "../capabilities";


export interface ToolSettings {
  policies: Record<Capability, PolicyMode>;
  allowed_roots: string[];
  shell_allowlist: string[];
  [key: string]: unknown;
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

export function PermissionsSection({ tools, onSave }: { tools: ToolSettings; onSave: (t: ToolSettings) => void }) {
  const [policies, setPolicies] = useState(tools.policies);
  const [roots, setRoots] = useState(tools.allowed_roots.join("\n"));
  const [allowlist, setAllowlist] = useState(tools.shell_allowlist.join("\n"));

  return (
    <section aria-labelledby="perm-title">
      <h3 id="perm-title">Permissions</h3>
      <p className="hint">
        Deleting or overwriting, sending data out, and purchases always ask, whatever is set here. So does anything
        risky in a chat that has read files, pages, or command output.
      </p>
      <div className="perm-grid">
        {(Object.keys(CAPABILITY_LABELS) as Capability[]).map((cap) => (
          <label key={cap} className="perm-row">
            <span>{CAPABILITY_LABELS[cap]}</span>
            <select
              value={policies[cap] ?? "ask"}
              onChange={(e) => setPolicies({ ...policies, [cap]: e.target.value as PolicyMode })}
            >
              <option value="allow">Allow</option>
              <option value="ask">Ask</option>
              <option value="deny">Never</option>
            </select>
          </label>
        ))}
      </div>
      <label className="stack">
        Folders the assistant may use (one per line)
        <textarea rows={3} value={roots} onChange={(e) => setRoots(e.target.value)} />
      </label>
      <label className="stack">
        Commands it may run (one per line; shells, deletion, and admin tools are always blocked)
        <textarea rows={4} value={allowlist} onChange={(e) => setAllowlist(e.target.value)} />
      </label>
      <div className="row">
        <button
          type="button"
          onClick={() => onSave({ ...tools, policies, allowed_roots: lines(roots), shell_allowlist: lines(allowlist) })}
        >
          Save permissions
        </button>
      </div>
    </section>
  );
}
