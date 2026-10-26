import { useCallback, useState } from "react";
import { useLoad } from "../useLoad";
import { LoadState } from "./LoadState";
import { Page } from "./Page";
import { ApiError, type Api } from "../api";
import type { Skill } from "../contracts";

const STATUS: Record<Skill["status"], string> = {
  candidate: "Learning",
  active: "Active",
  demoted: "Paused after failures",
};

function describeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

function SkillCard({ skill, api, onChanged }: { skill: Skill; api: Api; onChanged: () => void }) {
  const [name, setName] = useState(skill.name);
  const [triggers, setTriggers] = useState(skill.triggers.join("\n"));
  const [enabled, setEnabled] = useState(skill.enabled);
  const [error, setError] = useState<string | null>(null);
  const titleId = `skill-${skill.skill_id}`;

  const patch = async (body: Record<string, unknown>): Promise<boolean> => {
    try {
      await api.patchSkill(skill.skill_id, body);
      setError(null);
      onChanged();
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save.");
      return false;
    }
  };

  const toggle = async (next: boolean) => {
    setEnabled(next); // show the change right away; undo it if saving fails
    if (!(await patch({ enabled: next }))) setEnabled(!next);
  };

  const remove = async () => {
    if (!window.confirm(`Delete the skill “${skill.name}”?`)) return;
    await api.deleteSkill(skill.skill_id);
    onChanged();
  };

  return (
    <article className="skill" aria-labelledby={titleId}>
      <div className="row">
        <span className={`badge badge-${skill.status}`}>{STATUS[skill.status]}</span>
        <h3 id={titleId} className="skill-name">
          {skill.name}
        </h3>
      </div>
      <p className="hint">
        {skill.verified_successes} successful run{skill.verified_successes === 1 ? "" : "s"} · {skill.failures} failed
        {skill.params.length > 0 && ` · fills in: ${skill.params.map((p) => `{${p.name}}`).join(", ")}`}
      </p>
      <ol className="skill-steps">
        {skill.steps.map((step, i) => (
          <li key={i}>
            <code>{step.tool}</code> {describeArgs(step.args_template)}
            {step.checks.includes("verified") && <span className="hint"> · checked afterwards</span>}
          </li>
        ))}
      </ol>
      <label className="stack">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="stack">
        Phrases that run it (one per line, keep the {"{slots}"})
        <textarea rows={Math.min(4, triggers.split("\n").length + 1)} value={triggers} onChange={(e) => setTriggers(e.target.value)} />
      </label>
      {error && <p className="error-line">{error}</p>}
      <div className="row">
        <button type="button" onClick={() => void patch({ name, triggers: triggers.split("\n").filter((t) => t.trim()) })}>
          Save
        </button>
        <label className="radio">
          <input type="checkbox" checked={enabled} onChange={(e) => void toggle(e.target.checked)} />
          Enabled
        </label>
        {skill.status === "demoted" && (
          <button type="button" onClick={() => void patch({ reactivate: true })}>
            Re-enable
          </button>
        )}
        <button type="button" className="danger-link" onClick={() => void remove()}>
          Delete
        </button>
      </div>
    </article>
  );
}

export function SkillsPage({ api, onClose }: { api: Api; onClose: () => void }) {
  const load = useCallback(() => api.skills(), [api]);
  const skills = useLoad(load);
  const list = skills.data ?? [];
  return (
    <Page id="skills-title" title="Skills" onClose={onClose}>
      <p className="hint">
        When the assistant finishes a task with checked, successful steps, it saves the steps as a skill. After{" "}
        two good runs the skill replays instantly, with no AI call. Steps that delete, send, or buy still ask you
        every time.
      </p>
      <LoadState {...skills} empty={list.length === 0} onRetry={skills.reload} what="skills"
                 emptyText="No skills yet. They appear after the assistant completes tasks for you." />
      {list.map((s) => <SkillCard key={`${s.skill_id}-${s.verified_successes}-${s.status}`} skill={s} api={api} onChanged={skills.reload} />)}
    </Page>
  );
}
