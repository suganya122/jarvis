import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError, type Api } from "../api";
import type { Status } from "../contracts";
import { ControlSetupSection } from "./ControlSetupSection";
import { InstantSection, type RouterSettings } from "./InstantSection";
import { PermissionsSection, type ToolSettings } from "./PermissionsSection";
import { SpendingSection, type BackgroundSettings, type SpendingSettings } from "./SpendingSection";
import { DataSection } from "./DataSection";
import { ChatAppsSection } from "./ChatAppsSection";
import { BrowserSection, type BrowserSettings } from "./BrowserSection";
import { ContextSection, type ContextSettings } from "./ContextSection";
import { LoadState } from "./LoadState";
import { Page } from "./Page";

interface Props {
  voiceSection?: ReactNode;
  api: Api;
  status: Status | null;
  onClose: () => void;
  onChanged: () => void;
  appName: string;
  onDeleted: () => void;
  /** Bumped on plugin.status messages. */
  pluginTick: number;
}

type Provider = "anthropic" | "local";

interface LocalSettings {
  base_url: string;
  model: string;
}

export function SettingsPage(props: Props) {
  const { api, status, onClose, onChanged, voiceSection, appName, onDeleted, pluginTick } = props;
  const [loadError, setLoadError] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(() => {
    api.getSettings().then(
      (s) => (setSettings(s), setLoadError(null)),
      (err: unknown) => setLoadError(err instanceof ApiError ? err.message : "the app didn't answer"),
    );
  }, [api]);
  useEffect(load, [load]);

  const report = (err: unknown) => setMessage(err instanceof ApiError ? err.message : "Something went wrong.");

  const saveKey = async () => {
    try {
      await api.setApiKey(key.trim());
      setKey("");
      setMessage("API key saved to your OS keychain.");
      onChanged();
    } catch (err) {
      report(err);
    }
  };

  const removeKey = async () => {
    try {
      await api.deleteApiKey();
      setMessage("API key removed from the keychain.");
      onChanged();
    } catch (err) {
      report(err);
    }
  };

  const update = async (patch: Record<string, unknown>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    try {
      setSettings(await api.putSettings(next));
      setMessage("Settings saved.");
      onChanged();
    } catch (err) {
      report(err);
    }
  };

  const provider = (settings?.provider as Provider | undefined) ?? "anthropic";
  const local = settings?.local as LocalSettings | undefined;

  return (
    <Page id="settings-title" title="Settings" onClose={onClose}>
      {message && (
        <p role="status" className="page-status">
          {message}
        </p>
      )}
      <LoadState loading={settings === null} error={loadError} empty={false} onRetry={load} what="settings"
                 emptyText="" />

      <section aria-labelledby="key-title">
        <h3 id="key-title">Anthropic API key</h3>
        <p className="hint">
          Claude is the only paid part. Your key is stored in the OS keychain, never in files or logs.
          {status && (
            <>
              {" "}
              Current source: <strong>{status.api_key_source}</strong>.
            </>
          )}
        </p>
        {status?.keychain_error && <p className="error-line">Keychain unavailable: {status.keychain_error}</p>}
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void saveKey();
          }}
        >
          <label htmlFor="api-key" className="sr-only">
            API key
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            placeholder="sk-ant-…"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <button type="submit" className="primary" disabled={key.trim().length < 20}>
            Save key
          </button>
          {status?.api_key_source === "keychain" && (
            <button type="button" onClick={() => void removeKey()}>
              Remove
            </button>
          )}
        </form>
      </section>

      {settings && (
        <section aria-labelledby="provider-title">
          <h3 id="provider-title">Model provider</h3>
          <fieldset className="row">
            <legend className="sr-only">Provider</legend>
            {(["anthropic", "local"] as Provider[]).map((p) => (
              <label key={p} className="radio">
                <input
                  type="radio"
                  name="provider"
                  checked={provider === p}
                  onChange={() => void update({ provider: p })}
                />
                {p === "anthropic" ? "Claude (default)" : "Local model (free, optional)"}
              </label>
            ))}
          </fieldset>
          {provider === "local" && local && (
            <LocalForm key={`${local.base_url}|${local.model}`} local={local} onSave={(l) => void update({ local: { ...local, ...l } })} />
          )}
          {status && (
            <p className="hint">
              Status: {status.provider.ready ? "ready" : "not ready"}
              {status.provider.detail && ` (${status.provider.detail})`}
            </p>
          )}
        </section>
      )}

      {settings?.spending && settings.background ? (
        <SpendingSection
          key={JSON.stringify([settings.spending, settings.background])}
          spending={settings.spending as SpendingSettings}
          background={settings.background as BackgroundSettings}
          onSave={(patch) => void update(patch)}
        />
      ) : null}

      {settings?.browser ? (
        <BrowserSection
          key={JSON.stringify(settings.browser)}
          browser={settings.browser as BrowserSettings}
          onSave={(browser) => void update({ browser })}
        />
      ) : null}

      {settings?.router ? (
        <InstantSection
          key={JSON.stringify(settings.router)}
          router={settings.router as RouterSettings}
          onSave={(router) => void update({ router })}
        />
      ) : null}

      {voiceSection}

      {settings?.context ? (
        <ContextSection context={settings.context as ContextSettings} onSave={(context) => void update({ context })} />
      ) : null}

      <ControlSetupSection api={api} />

      {settings?.tools ? (
        <PermissionsSection
          key={JSON.stringify(settings.tools)}
          tools={settings.tools as ToolSettings}
          onSave={(tools) => void update({ tools })}
        />
      ) : null}

      <ChatAppsSection api={api} tick={pluginTick} />

      <DataSection api={api} appName={appName} onDeleted={onDeleted} />

    </Page>
  );
}

function LocalForm({ local, onSave }: { local: LocalSettings; onSave: (l: LocalSettings) => void }) {
  const [baseUrl, setBaseUrl] = useState(local.base_url);
  const [model, setModel] = useState(local.model);
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ base_url: baseUrl, model });
      }}
    >
      <label>
        Server URL (Ollama, LM Studio, llama.cpp, vLLM)
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </label>
      <label>
        Model name
        <input value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <button type="submit">Save local model</button>
    </form>
  );
}
