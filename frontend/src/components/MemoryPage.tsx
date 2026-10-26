import { useCallback, useEffect, useState } from "react";
import { useLoad } from "../useLoad";
import { LoadState } from "./LoadState";
import { Page } from "./Page";
import { ApiError, type Api } from "../api";
import type { Memory } from "../contracts";

function MemoryRow({ memory, api, onChanged }: { memory: Memory; api: Api; onChanged: (error?: string) => void }) {
  const [text, setText] = useState(memory.text);
  const changed = text.trim() !== memory.text;

  const run = async (p: Promise<unknown>) => {
    try {
      await p;
      onChanged();
    } catch (err) {
      onChanged(err instanceof ApiError ? err.message : "Couldn't save.");
    }
  };

  return (
    <li className="memory">
      <label className="sr-only" htmlFor={`mem-${memory.memory_id}`}>
        Memory
      </label>
      <input id={`mem-${memory.memory_id}`} value={text} maxLength={500} onChange={(e) => setText(e.target.value)} />
      {changed && (
        <button type="button" onClick={() => void run(api.editMemory(memory.memory_id, text))} disabled={text.trim().length < 2}>
          Save
        </button>
      )}
      <button
        type="button"
        className="danger-link"
        aria-label={`Delete “${memory.text}”`}
        onClick={() => void run(api.deleteMemory(memory.memory_id))}
      >
        Delete
      </button>
      {memory.source === "assistant" && <span className="hint">saved by the assistant</span>}
    </li>
  );
}

export function MemoryPage({ api, onClose }: { api: Api; onClose: () => void }) {
  const loadMemories = useCallback(() => api.memories(), [api]);
  const memories = useLoad(loadMemories);
  const [enabled, setEnabled] = useState(true);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then((s) => setEnabled((s.memory as { enabled?: boolean } | undefined)?.enabled ?? true), () => {});
  }, [api]);

  const changed = (message?: string) => {
    setError(message ?? null);
    memories.reload();
  };

  const add = async () => {
    try {
      await api.addMemory(draft.trim());
      setDraft("");
      changed();
    } catch (err) {
      changed(err instanceof ApiError ? err.message : "Couldn't save.");
    }
  };

  const toggle = async (next: boolean) => {
    setEnabled(next);
    try {
      const current = await api.getSettings();
      await api.putSettings({ ...current, memory: { ...(current.memory as object), enabled: next } });
    } catch {
      setEnabled(!next);
    }
  };

  return (
    <Page id="memory-title" title="Memory" onClose={onClose}>
      <p className="hint">
        Say “remember that …” in chat, or add notes here. They stay on this computer and are given to Claude with your
        questions, so keep secrets (passwords, card numbers) out of them. “Forget …” removes one.
      </p>
      <label className="radio">
        <input type="checkbox" checked={enabled} onChange={(e) => void toggle(e.target.checked)} />
        Use memories in chats
      </label>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <label htmlFor="memory-new" className="sr-only">
          New memory
        </label>
        <input
          id="memory-new"
          placeholder="e.g. My sister's name is Priya"
          value={draft}
          maxLength={500}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="primary" disabled={draft.trim().length < 2}>
          Add
        </button>
      </form>
      {error && <p className="error-line">{error}</p>}
      <LoadState {...memories} empty={(memories.data ?? []).length === 0} onRetry={memories.reload}
                 what="memories" emptyText="Nothing saved yet." />
      <ul className="memories">
        {memories.data?.map((m) => <MemoryRow key={`${m.memory_id}-${m.updated_at}`} memory={m} api={api} onChanged={changed} />)}
      </ul>
    </Page>
  );
}
