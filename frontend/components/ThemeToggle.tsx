"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "dark" | "light";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") {
      return "dark";
    }
    return localStorage.getItem("theme") === "light" ? "light" : "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  };

  return (
    <button onClick={toggleTheme} className="btn btn-outline" aria-label="切換顯示主題">
      {theme === "dark" ? <Sun size={20} /> : <Moon size={20} />}
      <span style={{ marginLeft: "8px" }}>{theme === "dark" ? "亮色" : "暗色"}</span>
    </button>
  );
}
