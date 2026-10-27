import { useCallback, useEffect, useState } from "react";
import { useLoad } from "../useLoad";
import { LoadState } from "./LoadState";
import { Page } from "./Page";
import { KnowledgeGraph } from "./KnowledgeGraph";
import { ApiError, type Api } from "../api";
import type { KnowledgeEntity, KnowledgeFact } from "../contractsHttp";

type Changed = (error?: string) => void;
interface Toggles {
  graph: boolean;
  learn: boolean;
}

function FactLine({ fact, api, onChanged }: { fact: KnowledgeFact; api: Api; onChanged: Changed }) {
  const forget = async () => {
    try {
      await api.deleteKnowledgeFact(fact.id);
      onChanged();
    } catch (err) {
      onChanged(err instanceof ApiError ? err.message : "Couldn't forget that.");
    }
  };
  return (
    <li className={fact.current ? "kg-fact" : "kg-fact kg-fact-old"}>
      <span>{fact.text}</span>
      {!fact.current && <span className="hint"> (older, replaced)</span>}
      {fact.quote && <span className="hint"> you said “{fact.quote}”</span>}
      <button type="button" className="danger-link" aria-label={`Forget “${fact.text}”`} onClick={() => void forget()}>
        Forget
      </button>
    </li>
  );
}

function EntityCard({
  entity,
  others,
  api,
  selected,
  onChanged,
}: {
  entity: KnowledgeEntity;
  others: KnowledgeEntity[];
  api: Api;
  selected: boolean;
  onChanged: Changed;
}) {
  const [sure, setSure] = useState(false);
  const [mergeInto, setMergeInto] = useState("");
  const [name, setName] = useState(entity.name);
  const run = async (p: Promise<unknown>) => {
    try {
      await p;
      onChanged();
    } catch (err) {
      onChanged(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  return (
    <li id={`kg-${entity.id}`} className={selected ? "kg-card kg-card-selected" : "kg-card"}>
      <h3>
        {entity.name} <span className="badge">{entity.type}</span>
        {entity.relation && <span className="badge">your {entity.relation}</span>}
      </h3>
      <div className="row">
        <label className="sr-only" htmlFor={`name-${entity.id}`}>
          Name
        </label>
        <input id={`name-${entity.id}`} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        {name.trim() !== entity.name && name.trim().length > 1 && (
          <button type="button" onClick={() => void run(api.renameKnowledge(entity.id, name))}>
            Rename
          </button>
        )}
      </div>
      {entity.aliases.length > 0 && <p className="hint">Also: {entity.aliases.join(", ")}</p>}
      <ul className="kg-facts">
        {entity.facts.length === 0 && <li className="hint">Nothing else yet.</li>}
        {entity.facts.map((f) => (
          <FactLine key={f.id} fact={f} api={api} onChanged={onChanged} />
        ))}
      </ul>
      <div className="row">
        {sure ? (
          <>
            <button type="button" className="danger-link" onClick={() => void run(api.deleteKnowledgeEntity(entity.id))}>
              Yes, forget everything about {entity.name}
            </button>
            <button type="button" onClick={() => setSure(false)}>
              Keep
            </button>
          </>
        ) : (
          <button type="button" className="danger-link" onClick={() => setSure(true)}>
            Forget {entity.name}
          </button>
        )}
        {others.length > 0 && (
          <>
            <label className="sr-only" htmlFor={`merge-${entity.id}`}>
              Same as
            </label>
            <select id={`merge-${entity.id}`} value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}>
              <option value="">Same as…</option>
              {others.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
            {mergeInto && (
              <button type="button" onClick={() => void run(api.mergeKnowledge(mergeInto, entity.id))}>
                Merge into {others.find((o) => o.id === mergeInto)?.name}
              </button>
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function KnowledgePage({ api, onClose }: { api: Api; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [history, setHistory] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toggles, setToggles] = useState<Toggles>({ graph: true, learn: true });
  const load = useCallback(() => api.knowledge(query.trim(), history), [api, query, history]);
  const knowledge = useLoad(load);

  useEffect(() => {
    api.getSettings().then(
      (s) => {
        const m = (s.memory ?? {}) as Partial<Toggles>;
        setToggles({ graph: m.graph ?? true, learn: m.learn ?? true });
      },
      () => {},
    );
  }, [api]);

  const toggle = async (key: keyof Toggles, value: boolean) => {
    setToggles((t) => ({ ...t, [key]: value }));
    try {
      const current = await api.getSettings();
      await api.putSettings({ ...current, memory: { ...(current.memory as object), [key]: value } });
    } catch {
      setToggles((t) => ({ ...t, [key]: !value }));
    }
  };

  const changed = (message?: string) => {
    setError(message ?? null);
    knowledge.reload();
  };
  const entities = knowledge.data?.entities ?? [];
  const you = knowledge.data?.you ?? [];
  const select = (id: string) => {
    setSelected(id);
    document.getElementById(`kg-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  return (
    <Page id="knowledge-title" title="Knowledge" onClose={onClose}>
      <p className="hint">
        What it has learned about your world from what you tell it: people, places, preferences. It learns only from
        your own words (never web pages or files), keeps it on this computer, and never stores passwords, card or phone
        numbers. Everything is here to correct or forget.
      </p>
      <label className="radio">
        <input type="checkbox" checked={toggles.learn} onChange={(e) => void toggle("learn", e.target.checked)} />
        Learn from what I say
      </label>
      <label className="radio">
        <input type="checkbox" checked={toggles.graph} onChange={(e) => void toggle("graph", e.target.checked)} />
        Use what it knows in chats
      </label>
      <div className="row">
        <label htmlFor="kg-search" className="sr-only">
          Search
        </label>
        <input
          id="kg-search"
          type="search"
          placeholder="Search people, places…"
          value={query}
          maxLength={100}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="radio">
          <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} />
          Show what changed
        </label>
      </div>
      {error && <p className="error-line">{error}</p>}
      <LoadState
        {...knowledge}
        empty={entities.length === 0 && you.length === 0}
        onRetry={knowledge.reload}
        what="what it knows"
        emptyText="Nothing yet. Tell it about the people in your life: “my brother Srinith prefers WhatsApp”."
      />
      {entities.length > 0 && <KnowledgeGraph entities={entities} selected={selected} onSelect={select} />}
      {you.length > 0 && (
        <section aria-labelledby="kg-you">
          <h3 id="kg-you">About you</h3>
          <ul className="kg-facts">
            {you.map((f) => (
              <FactLine key={f.id} fact={f} api={api} onChanged={changed} />
            ))}
          </ul>
        </section>
      )}
      <ul className="kg-cards">
        {entities.map((e) => (
          <EntityCard
            key={e.id}
            entity={e}
            others={entities.filter((o) => o.id !== e.id && o.type === e.type)}
            api={api}
            selected={selected === e.id}
            onChanged={changed}
          />
        ))}
      </ul>
    </Page>
  );
}
