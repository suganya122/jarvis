/** The typing box is off by default (it's a voice assistant). Settings turns it on; so does
 * `#typing=1` in the link (used by the browser tests). Kept per browser, not on the server. */
const KEY = "assistant.typing";
/** Read when the module loads: the login token is stripped from the link right after. */
const FROM_LINK = /(?:^#|&)typing=1(?:&|$)/.test(window.location.hash);

export function readTyping(): boolean {
  if (FROM_LINK) {
    saveTyping(true);
    return true;
  }
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function saveTyping(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    // private window: it just isn't remembered
  }
}
