import { useEffect, useState } from "react";
import { api } from "../api";
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
  }
  useEffect(() => { load(); }, []);

  async function switchTo(pid: string) {
    setBusy(true);
    try {
      await api.activateProject(pid);
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
          <div className="picker-menu-header">切换项目</div>
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
                    e.stopPropagation();
                    const ok = window.confirm(
                      `永久删除「${p.name}」？\n\n所有数据（策略 / 出稿 / 报告 / 数据指标）都会**不可恢复**地删除。`
                    );
                    if (!ok) return;
                    setBusy(true);
                    try {
                      await api.hardDeleteProject(p.project_id);
                      if (p.active) window.location.reload();
                      else await load();
                    } catch (err: any) {
                      alert("删除失败 ：" + err.message);
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
