import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { applyTheme } from "./theme";
import ConnectionBanner from "./components/ConnectionBanner";
import ProjectPicker from "./components/ProjectPicker";
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
  // up the active library's platform whenever it changes.
  useEffect(() => {
    let cancel = false;
    async function syncTheme() {
      try {
        const libs = await api.libraries();
        if (cancel) return;
        const active = libs.find((l: any) => l.active);
        applyTheme(active?.platform);
      } catch { applyTheme(undefined); }
    }
    syncTheme();
    // Re-sync periodically so the theme updates when a different lib is activated
    const t = setInterval(syncTheme, 5000);
    return () => { cancel = true; clearInterval(t); };
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">EZ</div>
          <div>EZAccountRise</div>
        </div>
        <ProjectPicker />
        <NavLink to="/reports" className={({isActive}) => isActive ? "active" : ""}>📊 分析报告 <span style={{fontSize: 10, color: "var(--muted)"}}>第 1 步</span></NavLink>
        <NavLink to="/strategy" className={({isActive}) => isActive ? "active" : ""}>🚀 起号策略 <span style={{fontSize: 10, color: "var(--muted)"}}>第 2 步</span></NavLink>
        <NavLink to="/composer" className={({isActive}) => isActive ? "active" : ""}>✍️ 出稿 <span style={{fontSize: 10, color: "var(--muted)"}}>第 3 步</span></NavLink>
        <NavLink to="/libraries" className={({isActive}) => isActive ? "active" : ""}>📥 资源库 · 上传</NavLink>
        <NavLink to="/dashboard" className={({isActive}) => isActive ? "active" : ""}>🗂️ 数据总览</NavLink>
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
        <Routes>
          <Route path="/" element={<Navigate to="/reports" replace />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/reports/:id" element={<InsightReport />} />
          <Route path="/strategy" element={<Strategy />} />
          <Route path="/strategy/:packId" element={<Strategy />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/composer" element={<Composer />} />
          <Route path="/drafts" element={<Drafts />} />
          <Route path="/drafts/:id" element={<DraftDetail />} />
          <Route path="/libraries" element={<Libraries />} />
          <Route path="/insight/:id" element={<InsightReport />} />
          <Route path="/integrated/:id" element={<IntegratedReport />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="card">404 · 页面不存在</div>} />
        </Routes>
      </main>
    </div>
  );
}
