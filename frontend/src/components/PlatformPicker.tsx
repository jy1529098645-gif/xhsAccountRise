/**
 * Top-level target-platform switcher. Lives in the left sidebar, directly
 * under ProjectPicker, so the user can flip the whole product between
 * "I'm targeting 小红书 today" and "I'm targeting 抖音 today" without
 * digging into resource-library settings.
 *
 * Reads/writes localStorage `studio.targetPlatform`. Downstream pages
 * (Composer / Strategy / Reports) consume this as their default platform
 * when running a new generation. Active library's platform still drives
 * RAG retrieval — this picker is about the AUTHORING target.
 *
 * Dispatches a `studio:targetPlatform` CustomEvent on change so pages that
 * mounted before the switch can react without a full page reload.
 */
import { useEffect, useState } from "react";
import { PLATFORM_GUIDES } from "../catalog";

const STORAGE_KEY = "studio.targetPlatform";
const DEFAULT_PLATFORM = "xiaohongshu";

const PLATFORM_BY_ID = Object.fromEntries(
  PLATFORM_GUIDES.map((p) => [p.id, p])
);

export function getTargetPlatform(): string {
  return localStorage.getItem(STORAGE_KEY) || DEFAULT_PLATFORM;
}

export function setTargetPlatform(platform: string): void {
  localStorage.setItem(STORAGE_KEY, platform);
  window.dispatchEvent(
    new CustomEvent("studio:targetPlatform", { detail: { platform } })
  );
}

/** Subscribe to target-platform changes. Returns the current value and a
 *  setter; updates re-render every subscriber via the window CustomEvent. */
export function useTargetPlatform(): [string, (p: string) => void] {
  const [platform, setPlatformState] = useState<string>(getTargetPlatform);
  useEffect(() => {
    const onChange = (e: Event) => {
      const ce = e as CustomEvent<{ platform: string }>;
      setPlatformState(ce.detail.platform);
    };
    window.addEventListener("studio:targetPlatform", onChange);
    return () =>
      window.removeEventListener("studio:targetPlatform", onChange);
  }, []);
  return [platform, setTargetPlatform];
}

export default function PlatformPicker() {
  const [platform, setPlatformLocal] = useTargetPlatform();
  const [open, setOpen] = useState(false);

  const cur = PLATFORM_BY_ID[platform] ?? PLATFORM_BY_ID["xiaohongshu"];

  function pick(p: string) {
    setPlatformLocal(p);
    setOpen(false);
  }

  return (
    <div className="platform-picker">
      <button
        className="picker-btn platform-picker-btn"
        onClick={() => setOpen(!open)}
        title="切换目标平台。Composer / 策略 / 报告 默认按这个平台生成"
      >
        <span style={{ fontSize: 14 }}>{cur.emoji}</span>
        <span className="picker-name">
          <span style={{ fontSize: 10, color: "var(--muted)", display: "block", lineHeight: 1 }}>
            目标平台
          </span>
          <span style={{ fontWeight: 600 }}>{cur.label}</span>
        </span>
        <span className="picker-arrow">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          <div className="picker-menu-header">切换目标平台</div>
          {PLATFORM_GUIDES.map((p) => (
            <div
              key={p.id}
              className={`picker-item ${
                p.id === platform ? "active" : ""
              }`}
              onClick={() => pick(p.id)}
            >
              <span style={{ fontSize: 14 }}>{p.emoji}</span>
              <span style={{ flex: 1 }}>{p.label}</span>
              {p.id === platform && <span style={{ fontSize: 11 }}>✓</span>}
            </div>
          ))}
          <div
            className="picker-menu-footer"
            style={{ fontSize: 10, color: "var(--muted)", padding: "6px 10px" }}
          >
            切换不影响已有资源库，只改默认目标
          </div>
        </div>
      )}
    </div>
  );
}
