import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "assistant.theme";

function readTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "system" ? value : "dark"; // dark unless the user chose otherwise
  } catch {
    return "dark";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // per-viewer convenience only
    }
  }, [theme]);

  return (
    <label className="theme">
      <span className="sr-only">Theme</span>
      <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
        <option value="system">Auto theme</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}
