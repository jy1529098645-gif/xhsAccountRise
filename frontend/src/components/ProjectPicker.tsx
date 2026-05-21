import { useEffect, useState } from "react";
import { api } from "../api";
import { humaniseError } from "../errors";
import type { ProjectDTO } from "../types";

export default function ProjectPicker() {
  const [projects, setProjects] = useState<ProjectDTO[]>([]);
  const [active, setActive] = useState<string>("default");
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmoji, setNewEmoji] = useState("🚀");
  const [busy, setBusy] = useState(false);

  async function load() {
    const r = await api.listProjects();
    setProjects(r.projects);
    setActive(r.active);
    // v0.61.15 ：把 active 项目 id 同步到 localStorage，其它模块的本地缓存
    // (Strategy DRAFT / autofill / propose 缓存 / Composer form 等) 按这个
    // id 拼成项目作用域的 storage key，避免跨项目串数据。
    try { localStorage.setItem("studio.activeProjectId", r.active); } catch { /* quota */ }
  }
  useEffect(() => { load(); }, []);

  async function switchTo(pid: string) {
    setBusy(true);
    try {
      await api.activateProject(pid);
      try { localStorage.setItem("studio.activeProjectId", pid); } catch { /* quota */ }
      // Force a hard reload so every page re-reads its scoped data.
      window.location.reload();
    } catch (e: any) {
      alert("切换失败：" + e.message);
      setBusy(false);
    }
  }

  async function create() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const r = await api.createProject(newName.trim(), "", newEmoji);
      setNewName(""); setCreating(false);
      await api.activateProject(r.project_id);
      try { localStorage.setItem("studio.activeProjectId", r.project_id); } catch { /* quota */ }
      window.location.reload();
    } catch (e: any) {
      alert("创建失败：" + e.message);
      setBusy(false);
    }
  }

  const cur = projects.find(p => p.project_id === active);

  return (
    <div className="project-picker">
      <button className="picker-btn" onClick={() => setOpen(!open)} disabled={busy}>
        <span style={{fontSize: 14}}>{cur?.emoji ?? "📁"}</span>
        <span className="picker-name">{cur?.name ?? "默认项目"}</span>
        <span className="picker-arrow">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          <div className="picker-menu-header">
            切换项目 · 共 {projects.length} 个
          </div>
          {projects.map(p => (
            <div key={p.project_id} className={`picker-item ${p.active ? "active" : ""}`}>
              <span style={{flex: 1, display: "flex", gap: 6, alignItems: "center", cursor: "pointer"}}
                onClick={() => p.project_id !== active && switchTo(p.project_id)}>
                <span style={{fontSize: 14}}>{p.emoji}</span>
                <span style={{flex: 1}}>{p.name}</span>
                {p.is_default && <span className="muted" style={{fontSize: 10}}>默认</span>}
                {p.active && <span style={{color: "var(--primary)"}}>★</span>}
              </span>
              {!p.is_default && (
                <button
                  className="ghost"
                  title="永久删除这个项目和它所有的数据"
                  style={{padding: "1px 6px", fontSize: 11, opacity: 0.5, color: "var(--danger)"}}
                  onClick={async (e) => {
                    // v0.61.5 ：用户要求删除时不再弹 confirm — 一点就删。
                    // 误点风险用户接受 ：项目删了还能再新建，数据丢了是用户自己的决定。
                    e.stopPropagation();
                    setBusy(true);
                    try {
                      await api.hardDeleteProject(p.project_id);
                      if (p.active) window.location.reload();
                      else await load();
                    } catch (err: any) {
                      // v0.63.3 ：之前 alert 里直接吐 `err.message` — 后端返回的是
                      // {"detail":"..."} 形式的 JSON 文本，用户看到一坨 JSON 误以为
                      // 「报错不让我删除」。改用 humaniseError 解析 FastAPI 的 detail。
                      // eslint-disable-next-line no-console
                      console.error("[ProjectPicker] hardDelete failed", err);
                      const friendly = humaniseError(err);
                      alert("删除失败 ：\n" + friendly);
                    } finally { setBusy(false); }
                  }}
                >✕</button>
              )}
            </div>
          ))}
          <div className="picker-divider" />
          {creating ? (
            <div className="picker-create">
              <input value={newEmoji} onChange={e => setNewEmoji(e.target.value)}
                style={{width: 36, textAlign: "center"}} maxLength={2} />
              <input value={newName} onChange={e => setNewName(e.target.value)}
                placeholder="新项目名" autoFocus
                onKeyDown={e => { if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(false); }}
                style={{flex: 1}} />
              <button onClick={create} disabled={!newName.trim() || busy}
                style={{padding: "4px 10px", fontSize: 11}}>建</button>
            </div>
          ) : (
            <div className="picker-item picker-create-trigger" onClick={() => setCreating(true)}>
              + 新项目
            </div>
          )}
        </div>
      )}
    </div>
  );
}
