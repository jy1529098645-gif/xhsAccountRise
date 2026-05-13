import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import ConnectionBanner from "./components/ConnectionBanner";
import Dashboard from "./pages/Dashboard";
import Analysis from "./pages/Analysis";
import Composer from "./pages/Composer";
import Drafts from "./pages/Drafts";
import DraftDetail from "./pages/DraftDetail";
import Libraries from "./pages/Libraries";
import Settings from "./pages/Settings";
import Strategy from "./pages/Strategy";

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

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">A</div>
          <div>AcademiCats · Studio</div>
        </div>
        <NavLink to="/strategy" className={({isActive}) => isActive ? "active" : ""}>🚀 起号策略 <span style={{fontSize: 10, color: "var(--muted)"}}>第 1 步</span></NavLink>
        <NavLink to="/libraries" className={({isActive}) => isActive ? "active" : ""}>📥 资源库 · 上传</NavLink>
        <NavLink to="/composer" className={({isActive}) => isActive ? "active" : ""}>✍️ 出稿</NavLink>
        <NavLink to="/dashboard" className={({isActive}) => isActive ? "active" : ""}>📊 数据总览</NavLink>
        <NavLink to="/analysis" className={({isActive}) => isActive ? "active" : ""}>🧬 爆款分析</NavLink>
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
          <Route path="/" element={<Navigate to="/strategy" replace />} />
          <Route path="/strategy" element={<Strategy />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/composer" element={<Composer />} />
          <Route path="/drafts" element={<Drafts />} />
          <Route path="/drafts/:id" element={<DraftDetail />} />
          <Route path="/libraries" element={<Libraries />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="card">404 · 页面不存在</div>} />
        </Routes>
      </main>
    </div>
  );
}
