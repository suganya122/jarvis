import type { ReactNode } from "react";

export type View = "home" | "chat" | "skills" | "tasks" | "automations" | "memory" | "audit" | "settings" | "overview";

const ITEMS: { view: View; label: string; narrowOnly?: boolean }[] = [
  { view: "home", label: "Voice" },
  { view: "chat", label: "History" },
  { view: "skills", label: "Skills" },
  { view: "tasks", label: "Background agents" },
  { view: "automations", label: "Automations" },
  { view: "memory", label: "Memory" },
  { view: "audit", label: "Audit log" },
  { view: "settings", label: "Settings" },
  { view: "overview", label: "Overview", narrowOnly: true },
];

interface Props {
  appName: string;
  view: View;
  onView: (view: View) => void;
  liveTasks: number;
  /** The chat list, shown under the navigation. */
  chats: ReactNode;
}

export function Sidebar({ appName, view, onView, liveTasks, chats }: Props) {
  return (
    <aside className="sidebar">
      <h1 className="brand">{appName}</h1>
      <nav className="nav" aria-label="Main">
        <ul>
          {ITEMS.map((item) => {
            const running = item.view === "tasks" && liveTasks > 0;
            return (
              <li key={item.view} className={item.narrowOnly ? "only-narrow" : undefined}>
                <button
                  type="button"
                  aria-current={view === item.view ? "page" : undefined}
                  aria-label={running ? `${item.label}, ${liveTasks} running` : undefined}
                  onClick={() => onView(item.view)}
                >
                  {item.label}
                  {running && <span className="count">{liveTasks}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      {chats}
    </aside>
  );
}
