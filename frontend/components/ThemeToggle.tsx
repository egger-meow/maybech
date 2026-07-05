"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "dark" | "light";

const themeEvent = "maybech-theme-change";

function readTheme(): Theme {
  try {
    return localStorage.getItem("theme") === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("theme", theme);
  } catch {
    // Keep the visual theme for this document even if storage is unavailable.
  }
}

function subscribeTheme(onChange: () => void): () => void {
  const handleStorage = (event: StorageEvent) => {
    if (event.key !== "theme") return;
    applyTheme(readTheme());
    onChange();
  };

  window.addEventListener("storage", handleStorage);
  window.addEventListener(themeEvent, onChange);

  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener(themeEvent, onChange);
  };
}

function getThemeSnapshot(): Theme | null {
  return readTheme();
}

function getServerThemeSnapshot(): Theme | null {
  return null;
}

export default function ThemeToggle() {
  const theme = useSyncExternalStore(
    subscribeTheme,
    getThemeSnapshot,
    getServerThemeSnapshot,
  );

  const toggleTheme = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    window.dispatchEvent(new Event(themeEvent));
  };

  if (theme === null) {
    return (
      <button type="button" className="btn btn-outline" aria-label="切換顯示主題">
        <span>主題</span>
      </button>
    );
  }

  return (
    <button type="button" onClick={toggleTheme} className="btn btn-outline" aria-label="切換顯示主題">
      {theme === "dark" ? <Sun size={20} /> : <Moon size={20} />}
      <span style={{ marginLeft: "8px" }}>{theme === "dark" ? "亮色" : "暗色"}</span>
    </button>
  );
}