import { useCallback, useEffect, useState } from "react";
import { ApiError, type Api } from "../api";
import type { PluginField, PluginInfo } from "../contracts";
import { CAPABILITY_LABELS } from "../capabilities";

const STATE: Record<PluginInfo["state"], string> = {
  starting: "Starting",
  connected: "Connected",
  error: "Problem",
  stopped: "Off",
};

function Secret({ field, isSet, onSave, onRemove }: {
  field: PluginField;
  isSet: boolean;
  onSave: (value: string) => void;
  onRemove: () => void;
}) {
  const [value, setValue] = useState("");
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(value.trim());
        setValue("");
      }}
    >
      <label>
        {field.label[0].toUpperCase() + field.label.slice(1)} {isSet && <span className="hint">(saved in your OS keychain)</span>}
        <input type="password" autoComplete="off" value={value} placeholder={isSet ? "••••••••" : ""}
               onChange={(e) => setValue(e.target.value)} />
      </label>
      {field.help && <span className="hint">{field.help}</span>}
      <span className="row">
        <button type="submit" disabled={!value.trim()}>
          Save {field.label}
        </button>
        {isSet && (
          <button type="button" className="danger-link" onClick={onRemove}>
            Remove
          </button>
        )}
      </span>
    </form>
  );
}

function PluginCard({ plugin, api, onChanged }: { plugin: PluginInfo; api: Api; onChanged: () => void }) {
  const m = plugin.manifest;
  const [code, setCode] = useState<string | null>(null);
  const [options, setOptions] = useState(plugin.options);
  const [error, setError] = useState<string | null>(null);
  const [enabled, setEnabled] = useState(plugin.enabled);

  const run = async (p: Promise<unknown>) => {
    try {
      await p;
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
    onChanged();
  };

  const toggle = (enabled: boolean) => {
    if (enabled) {
      const can = m.capabilities.map((c) => CAPABILITY_LABELS[c].toLowerCase()).join(", ") || "nothing extra";
      const origin = plugin.first_party ? "" : "\n\nThis plugin was not made by this project. Only enable plugins you trust.";
      const ok = window.confirm(
        `Turn on ${m.display_name}? It runs as a separate program on this computer, with your permissions. ` +
          `It needs: ${can}.\n\nPeople can use it only from accounts you pair, and anything that changes something ` +
          `asks for approval every time.${origin}`,
      );
      if (!ok) return;
    }
    setEnabled(enabled); // show it right away; the reload afterwards has the truth
    void run(api.setPluginEnabled(m.name, enabled));
  };

  const pair = async () => {
    try {
      const result = await api.pairPlugin(m.name);
      setCode(result.code);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create a code.");
    }
  };

  const advanced = m.options.filter((o) => o.advanced);
  return (
    <article className="skill" aria-labelledby={`plugin-${m.name}`}>
      <div className="row">
        <span className={`badge badge-plugin-${plugin.state}`}>{STATE[plugin.state]}</span>
        <h4 id={`plugin-${m.name}`} className="skill-name">
          {m.display_name}
        </h4>
        <label className="radio">
          <input type="checkbox" checked={enabled} onChange={(e) => toggle(e.target.checked)} />
          On
        </label>
      </div>
      {plugin.detail && <p className={plugin.state === "error" ? "error-line" : "hint"}>{plugin.detail}</p>}
      <p className="hint">{m.description}</p>
      {!plugin.first_party && <p className="approval-warn">Third-party plugin: it runs with your permissions.</p>}
      {m.secrets.map((f) => (
        <Secret key={f.name} field={f} isSet={plugin.secrets_set[f.name] ?? false}
                onSave={(v) => void run(api.setPluginSecret(m.name, f.name, v))}
                onRemove={() => void run(api.deletePluginSecret(m.name, f.name))} />
      ))}
      <h5>Paired accounts</h5>
      {plugin.paired.length === 0 && <p className="hint">None yet. Only paired accounts can talk to the assistant.</p>}
      <ul className="memories">
        {plugin.paired.map((a) => (
          <li key={a.user_id} className="memory">
            <span>{a.name ? `@${a.name}` : a.user_id}</span>
            <button type="button" className="danger-link" onClick={() => void run(api.unpair(m.name, a.user_id))}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => void pair()} disabled={plugin.state !== "connected"}>
        Pair an account
      </button>
      {code && (
        <p role="status" className="pair-code">
          In {m.display_name}, send your bot: <code>/pair {code}</code> (valid for 10 minutes)
        </p>
      )}
      {advanced.length > 0 && (
        <details>
          <summary>Advanced</summary>
          {advanced.map((f) => (
            <label key={f.name} className="stack">
              {f.label}
              <input value={options[f.name] ?? ""} onChange={(e) => setOptions({ ...options, [f.name]: e.target.value })} />
              <span className="hint">{f.help}</span>
            </label>
          ))}
          <button type="button" onClick={() => void run(api.setPluginOptions(m.name, options))}>
            Save
          </button>
        </details>
      )}
      {error && <p className="error-line">{error}</p>}
    </article>
  );
}

/** `tick` changes when a plugin.status message arrives, so the list stays live. */
export function ChatAppsSection({ api, tick }: { api: Api; tick: number }) {
  const [plugins, setPlugins] = useState<PluginInfo[] | null>(null);
  const load = useCallback(() => {
    api.plugins().then(setPlugins, () => setPlugins([]));
  }, [api]);
  useEffect(load, [load, tick]);

  return (
    <section aria-labelledby="chat-apps-title">
      <h3 id="chat-apps-title">Chat apps</h3>
      <p className="hint">
        Talk to the assistant from your phone. Off by default; requests from chat apps that change anything always
        ask you first, with Approve / Deny buttons in the chat.
      </p>
      {plugins?.length === 0 && <p>No chat-app plugins found.</p>}
      {plugins?.map((p) => <PluginCard key={`${p.manifest.name}-${p.enabled}-${p.state}-${p.paired.length}`} plugin={p} api={api} onChanged={load} />)}
    </section>
  );
}
