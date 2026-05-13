// Platform-driven color theming. Sets `data-platform` on <html> so CSS can
// swap the --primary / --primary-soft / --primary-hover variables. Keeping
// CSS in styles.css; this module just toggles the data attribute.

export interface ThemeSpec {
  primary: string;
  primaryHover: string;
  primarySoft: string;
  accent?: string;
}

export const PLATFORM_THEMES: Record<string, ThemeSpec> = {
  xiaohongshu: { primary: "#ff2442", primaryHover: "#e51d3a", primarySoft: "#ffeef1" },
  douyin:      { primary: "#fe2c55", primaryHover: "#d92148", primarySoft: "#fff0f3", accent: "#25f4ee" },
  kuaishou:    { primary: "#ff8801", primaryHover: "#e67500", primarySoft: "#fff5e6" },
  bilibili:    { primary: "#fb7299", primaryHover: "#e9577f", primarySoft: "#fef0f5" },
  youtube:     { primary: "#ff0033", primaryHover: "#cc002a", primarySoft: "#ffe9ed" },
  reddit:      { primary: "#ff4500", primaryHover: "#d63a00", primarySoft: "#fff0e5" },
  x:           { primary: "#1d9bf0", primaryHover: "#1a8cd8", primarySoft: "#e8f5fe" },
  other:       { primary: "#6b7280", primaryHover: "#4b5563", primarySoft: "#f3f4f6" },
};

export function applyTheme(platform: string | undefined) {
  const id = platform && PLATFORM_THEMES[platform] ? platform : "xiaohongshu";
  document.documentElement.dataset.platform = id;
  const theme = PLATFORM_THEMES[id];
  const root = document.documentElement.style;
  root.setProperty("--primary", theme.primary);
  root.setProperty("--primary-hover", theme.primaryHover);
  root.setProperty("--primary-soft", theme.primarySoft);
  if (theme.accent) root.setProperty("--accent", theme.accent);
}
