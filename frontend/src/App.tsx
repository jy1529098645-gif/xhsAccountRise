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
import Benchmarks from "./pages/Benchmarks";

// Re-keys the ErrorBoundary by pathname so a crashed page auto-resets
// the boundary when the user navigates somewhere else.
function RouteErrorBoundary({children}: {children: ReactNode}) {
  const loc = useLocation();
  return <ErrorBoundary key={loc.pathname}>{children}</ErrorBoundary>;
}

const MORE_ROUTES = ["/benchmarks", "/dashboard", "/analysis", "/drafts", "/settings"];

export default function App() {
  const location = useLocation();
  const [connected, setConnected] = useState<boolean>(api.isConnected());
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  // 停在「更多」里的页面时自动展开，避免当前激活项被折叠藏起来。
  const onMoreRoute = MORE_ROUTES.some(p => location.pathname.startsWith(p));
  // v0.66 ：侧边栏凝练 — 主线 3 步常驻，次级入口收进可折叠「更多」。
  const [moreOpen, setMoreOpen] = useState<boolean>(() => {
    try { return localStorage.getItem("studio.nav.moreOpen") === "1"; } catch { return false; }
  });
  function toggleMore() {
    setMoreOpen(v => {
      const next = !v;
      try { localStorage.setItem("studio.nav.moreOpen", next ? "1" : "0"); } catch { /* quota */ }
      return next;
    });
  }

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
        {/* v0.66 ：工作流凝练为 3 条主线 ：① 分析 → ② 起号工作台（策略+出稿+
            快速生成合一）→ ③ 复盘。其余次级入口收进「更多」折叠区，砍掉视觉长度。 */}
        <NavLink to="/reports" className={({isActive}) => isActive ? "active" : ""}>📊 ① 分析报告</NavLink>

        <div className="nav-group-label">② 起号工作台</div>
        <NavLink to="/strategy" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>🚀 起号策略</NavLink>
        <NavLink to="/composer" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>✍️ 出稿</NavLink>
        <NavLink to="/quick" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>⚡ 快速生成</NavLink>

        <NavLink to="/retrospective" className={({isActive}) => isActive ? "active" : ""}>📊 ③ 复盘</NavLink>
        <RunningJobsIndicator />

        <div className="nav-divider" />
        <div className="nav-more-toggle" onClick={toggleMore}
             role="button" tabIndex={0}
             onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleMore(); } }}>
          <span>⋯ 更多</span>
          <span style={{fontSize: 11}}>{(moreOpen || onMoreRoute) ? "▴" : "▾"}</span>
        </div>
        {(moreOpen || onMoreRoute) && (
          <>
            {/* v0.64 ：对标账号 — 从已上传 library 里挑账号 ，retrieve 时加权 */}
            <NavLink to="/benchmarks" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>🎯 对标账号</NavLink>
            <NavLink to="/dashboard" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>🗂️ 数据总览</NavLink>
            <NavLink to="/analysis" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>🧬 爆款分析</NavLink>
            <NavLink to="/drafts" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>📝 历史出稿</NavLink>
            <NavLink to="/settings" className={({isActive}) => (isActive ? "active nav-sub" : "nav-sub")}>⚙️ 设置</NavLink>
          </>
        )}

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
            <Route path="/benchmarks" element={<Benchmarks />} />
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
