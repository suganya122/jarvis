export interface ContextSettings {
  selection: boolean;
  clipboard: boolean;
  pin_minutes: number;
}

/** What the voice shortcut notes about where you are ("this screen", "here"). */
export function ContextSection({ context, onSave }: { context: ContextSettings; onSave: (c: ContextSettings) => void }) {
  return (
    <section aria-labelledby="context-title">
      <h3 id="context-title">“This” and “here”</h3>
      <p className="hint">
        When you press the voice shortcut, the assistant notes the screen your mouse is on and the window you're in, so
        “read this screen” or “do this here” means exactly that. Other screens are only looked at when you name them
        (“my other screen”). Say “no, the other one” to switch. Blocklisted apps are never read.
      </p>
      <label className="radio">
        <input type="checkbox" checked={context.selection} onChange={(e) => onSave({ ...context, selection: e.target.checked })} />
        Include the text I've selected
      </label>
      <label className="radio">
        <input type="checkbox" checked={context.clipboard} onChange={(e) => onSave({ ...context, clipboard: e.target.checked })} />
        Include my clipboard (off by default; anything that looks like a password or card number is left out)
      </label>
    </section>
  );
}
