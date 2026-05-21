import { ReactNode, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { applyTheme } from "./theme";
import ConnectionBanner from "./components/ConnectionBanner";
import ErrorBoundary from "./components/ErrorBoundary";
import ProjectPicker from "./components/ProjectPicker";
import PlatformPicker from "./components/PlatformPicker";
import RunningJobsIndicator from "./components/RunningJobsIndicator";
import Dashboard from "./pages/Dashboard";
import Analysis from "./pages/Analysis";
import Composer from "./pages/Composer";
import Drafts from "./pages/Drafts";
import DraftDetail from "./pages/DraftDetail";
import Libraries from "./pages/Libraries";
import Settings from "./pages/Settings";
import Strategy from "./pages/Strategy";
import InsightReport from "./pages/InsightReport";
import IntegratedReport from "./pages/IntegratedReport";
import Reports from "./pages/Reports";
import Retrospective from "./pages/Retrospective";
import QuickGenerate from "./pages/QuickGenerate";

// Re-keys the ErrorBoundary by pathname so a crashed page auto-resets
// the boundary when the user navigates somewhere else.
function RouteErrorBoundary({children}: {children: ReactNode}) {
  const loc = useLocation();
  return <ErrorBoundary key={loc.pathname}>{children}</ErrorBoundary>;
}

export default function App() {
  const [connected, setConnected] = useState<boolean>(api.isConnected());
  const [healthOk, setHealthOk] = useState<boolean | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      if (!api.isConnected()) {
        setHealthOk(null); setConnected(false); return;
      }
      const h = await api.health();
      if (!cancel) { setHealthOk(h.ok); setConnected(api.isConnected()); }
    })();
    return () => { cancel = true; };
  }, []);

  // Theme: re-apply on every render. Cheap (just sets CSS vars) and picks
  // up the active library's platform whenever it changes. Throttled to
  // 60s (theme rarely changes) and paused entirely when the tab is hidden
  // — previously this fired every 5s × N tabs, saturating uvicorn.
  useEffect(() => {
    let cancel = false;
    async function syncTheme() {
      if (document.hidden) return;
      try {
        const libs = await api.libraries();
        if (cancel) return;
        const active = libs.find((l: any) => l.active);
        applyTheme(active?.platform);
      } catch { applyTheme(undefined); }
    }
    syncTheme();
    const t = setInterval(syncTheme, 60_000);
    // Re-sync immediately when the tab becomes visible again (so a lib
    // change made in another tab is picked up promptly).
    function onVisible() { if (!document.hidden) syncTheme(); }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancel = true; clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">EZ</div>
          <div>EZAccountRise</div>
        </div>
        <ProjectPicker />
        <PlatformPicker />
        <NavLink to="/reports" className={({isActive}) => isActive ? "active" : ""}>📊 分析报告 <span style={{fontSize: 10, color: "var(--muted)"}}>第 1 步</span></NavLink>
        {/* v0.62.6 ：板块顺序 = 分析报告 → 起号策略 → 出稿 → 复盘。
            起号策略 = 看时间线大纲 + 备选 picker。
            出稿 = 创建 pack（goal/input/directions/expand）+ 多 agent 写每篇。 */}
        <NavLink to="/strategy" className={({isActive}) => isActive ? "active" : ""}>🚀 起号策略 <span style={{fontSize: 10, color: "var(--muted)"}}>第 2 步</span></NavLink>
        <NavLink to="/composer" className={({isActive}) => isActive ? "active" : ""}>✍️ 出稿 <span style={{fontSize: 10, color: "var(--muted)"}}>第 3 步</span></NavLink>
        {/* 快速生成 = 单 LLM 一键出稿，独立于主流程，仅依赖用户上传的报告 + DNA。
            放在复盘前面 — 用户写完主流程后还想临时来一篇时最顺手。 */}
        <NavLink to="/quick" className={({isActive}) => isActive ? "active" : ""}>⚡ 快速生成 <span style={{fontSize: 10, color: "var(--muted)"}}>可选</span></NavLink>
        <NavLink to="/retrospective" className={({isActive}) => isActive ? "active" : ""}>📊 复盘 <span style={{fontSize: 10, color: "var(--muted)"}}>第 4 步</span></NavLink>
        <NavLink to="/dashboard" className={({isActive}) => isActive ? "active" : ""}>🗂️ 数据总览</NavLink>
        <RunningJobsIndicator />
        <NavLink to="/analysis" className={({isActive}) => isActive ? "active" : ""}>🧬 爆款分析（粗粒度）</NavLink>
        <NavLink to="/drafts" className={({isActive}) => isActive ? "active" : ""}>📝 历史出稿</NavLink>
        <NavLink to="/settings" className={({isActive}) => isActive ? "active" : ""}>⚙️ 设置</NavLink>

        <div className={connected && healthOk ? "conn ok" : "conn off"}>
          <span className="dot"></span>
          {connected && healthOk
            ? "已连接本地后端"
            : connected
              ? "后端无响应"
              : "演示模式 (静态)"}
        </div>
        <div className="footer">
          v0.2 · multi-agent<br/>
          <a href="https://github.com/jy1529098645-gif/xhsAccountRise" target="_blank" rel="noreferrer">GitHub</a>
        </div>
      </aside>

      <main className="main">
        <ConnectionBanner />
        <RouteErrorBoundary>
          <Routes>
            <Route path="/" element={<Navigate to="/reports" replace />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/reports/:id" element={<InsightReport />} />
            <Route path="/strategy" element={<Strategy />} />
            <Route path="/strategy/:packId" element={<Strategy />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/analysis" element={<Analysis />} />
            <Route path="/composer" element={<Composer />} />
            <Route path="/quick" element={<QuickGenerate />} />
            <Route path="/retrospective" element={<Retrospective />} />
            <Route path="/drafts" element={<Drafts />} />
            <Route path="/drafts/:id" element={<DraftDetail />} />
            {/* v0.54: /libraries is folded into /settings#libraries.
                Keep redirect for shared links + Reports.tsx anchor links. */}
            <Route path="/libraries" element={<Navigate to="/settings#libraries" replace />} />
            <Route path="/insight/:id" element={<InsightReport />} />
            <Route path="/integrated/:id" element={<IntegratedReport />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="card">404 · 页面不存在</div>} />
          </Routes>
        </RouteErrorBoundary>
      </main>
    </div>
  );
}
