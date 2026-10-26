import { useState } from "react";

export interface BrowserSettings {
  mode: "isolated" | "own";
  show_window: boolean;
  channel: "auto" | "chrome" | "msedge" | "chromium";
  cdp_url: string;
  allow_private_hosts: boolean;
  min_seconds_between_pages: number;
}

/** Remount (key) when saved settings change: the form starts from what's stored. */
export function BrowserSection({ browser, onSave }: { browser: BrowserSettings; onSave: (b: BrowserSettings) => void }) {
  const [draft, setDraft] = useState(browser);
  const set = (patch: Partial<BrowserSettings>) => setDraft({ ...draft, ...patch });
  return (
    <section aria-labelledby="browser-title">
      <h3 id="browser-title">Browser (web tasks)</h3>
      <p className="hint">
        The assistant can search, compare and fill in forms on websites. It never pays, books or makes a final submit
        itself: it shows you a summary and you click the last button. CAPTCHAs and sign-in codes are always yours. Page
        text is treated as untrusted and can't give it permissions.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(draft);
        }}
      >
        <fieldset className="stack">
          <legend>Which browser</legend>
          <label className="radio">
            <input type="radio" name="bmode" checked={draft.mode === "isolated"} onChange={() => set({ mode: "isolated" })} />
            Its own browser (recommended): separate from yours, no access to your accounts
          </label>
          <label className="radio">
            <input type="radio" name="bmode" checked={draft.mode === "own"} onChange={() => set({ mode: "own" })} />
            My own Chrome, where I'm signed in (every change asks first)
          </label>
        </fieldset>
        {draft.mode === "own" && (
          <label className="stack">
            Your Chrome's debugging address
            <input value={draft.cdp_url} onChange={(e) => set({ cdp_url: e.target.value })} />
            <span className="hint">
              Start Chrome with <code>--remote-debugging-port=9222</code> (only this computer can reach it). The
              assistant works in a new tab and never sees your saved passwords.
            </span>
          </label>
        )}
        <label className="radio">
          <input type="checkbox" checked={draft.show_window} onChange={(e) => set({ show_window: e.target.checked })} />
          Show the browser window while it works (otherwise it appears only when it hands over to you)
        </label>
        <label className="radio">
          <input
            type="checkbox"
            checked={draft.allow_private_hosts}
            onChange={(e) => set({ allow_private_hosts: e.target.checked })}
          />
          Allow pages on this computer or the local network (router, NAS…)
        </label>
        <label className="stack">
          Browser program
          <select value={draft.channel} onChange={(e) => set({ channel: e.target.value as BrowserSettings["channel"] })}>
            <option value="auto">Automatic (Chrome, then Edge, then Chromium)</option>
            <option value="chrome">Google Chrome</option>
            <option value="msedge">Microsoft Edge</option>
            <option value="chromium">Chromium (run: python -m playwright install chromium)</option>
          </select>
        </label>
        <button type="submit">Save browser settings</button>
      </form>
    </section>
  );
}
