import type { Capability } from "./contracts";

/** Plain-language names for permissions, used in Settings and when enabling plugins. */
export const CAPABILITY_LABELS: Record<Capability, string> = {
  "fs.read": "Read files",
  "fs.write": "Create or change files",
  "shell.run": "Run commands",
  "net.fetch": "Open links",
  "input.control": "Control apps (open, click, type, keys)",
  "screen.read": "See windows and the screen",
  "send.external": "Send messages or data out",
  purchase: "Make purchases",
  schedule: "Schedule things (routines, watchers)",
  memory: "Save or delete memories",
  browser: "Use the web browser (search, fill in forms)",
};
