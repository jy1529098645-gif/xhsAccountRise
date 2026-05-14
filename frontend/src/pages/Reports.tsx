import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import ProgressTimeline, { Stage as TimelineStage } from "../components/ProgressTimeline";
import NextStepCard from "../components/NextStepCard";
import { humaniseError } from "../errors";
import { isAborted } from "../api";
import { GITHUB_REPO } from "../catalog";
import type { Library, Platform } from "../types";

const UPLOAD_STAGES: TimelineStage[] = [
  { label: "🤖 读取你的数据库", durationSec: 5,
    sub: "AI 正在打开 SQLite 看里面有什么表 / 列" },
  { label: "🤖 看懂数据格式（schema 适配）", durationSec: 15,
    sub: "如果不是标准小红书格式，AI 会自动把你的字段映射过来" },
  { label: "🤖 提取爆款 DNA（统计分析）", durationSec: 20,
    sub: "标题 hook / 蓝海词 / 发布时段 / 评论需求 / 字数互动…" },
  { label: "🤖 Claude 独立写一份报告", durationSec: 45,
    sub: "Anthropic 的视角" },
  { label: "🤖 OpenAI 独立写一份报告", durationSec: 45,
    sub: "GPT-4o 的视角（并行进行中）" },
  { label: "🤖 双方互相评审 + 主编整合共识", durationSec: 40,
    sub: "只保留双方都认可的洞察，分歧单列" },
];

const REANALYZE_STAGES: TimelineStage[] = [
  { label: "🤖 Claude 独立写一份报告", durationSec: 45, sub: "Anthropic 的视角" },
  { label: "🤖 OpenAI 独立写一份报告", durationSec: 45, sub: "GPT-4o 的视角" },
  { label: "🤖 双方互相评审 + 主编整合共识", durationSec: 40,
    sub: "只保留双方都认可的洞察" },
];

interface ReportSummary {
  report_id: string;
  library_id: string;
  created_at: number;
  status: string;
  elapsed_s: number | null;
}

interface ExternalRow {
  report_id: string; name: string; source: string; format: string;
  content_chars: number; uploaded_at: number; library_id: string | null;
}

interface IntegratedRow {
  integrated_id: string; library_id: string | null; created_at: number;
  status: string; source_ids: string[]; elapsed_s: number | null; error: string | null;
}

type Stage = "idle" | "uploading" | "analyzing-dna" | "running-insight" | "done";

export default function Reports() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [stage, setStage] = useState<Stage>("idle");
  const [progress, setProgress] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [selectedLibId, setSelectedLibId] = useState<string>("");
  const [dragOver, setDragOver] = useState(false);
  const [pendingPlatform, setPendingPlatform] = useState<string>("auto");
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  // ---- External / integrated reports state ------------------------------
  const [externals, setExternals] = useState<ExternalRow[]>([]);
  const [integrated, setIntegrated] = useState<IntegratedRow[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadContent, setUploadContent] = useState("");
  const [uploadBusy, setUploadBusy] = useState(false);
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set());
  const [integrating, setIntegrating] = useState(false);
  const [includeOwnConsensus, setIncludeOwnConsensus] = useState(true);
  const textFileRef = useRef<HTMLInputElement>(null);
  const insightAbortRef = useRef<AbortController | null>(null);
  const integrateAbortRef = useRef<AbortController | null>(null);
  function pauseInsight() { insightAbortRef.current?.abort(); }
  function pauseIntegrate() { integrateAbortRef.current?.abort(); }

  // Per-file feedback for the current session — user explicitly asked to
  // see "uploaded which files / did they succeed".
  interface UploadLogItem {
    id: string; name: string; status: "uploading" | "ok" | "fail";
    chars?: number; format?: string; warn?: string; error?: string;
  }
  const [uploadLog, setUploadLog] = useState<UploadLogItem[]>([]);
  function pushLog(item: Omit<UploadLogItem, "id">) {
    const id = `u_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    setUploadLog(prev => [{ ...item, id }, ...prev].slice(0, 8));
    return id;
  }
  function updateLog(id: string, patch: Partial<UploadLogItem>) {
    setUploadLog(prev => prev.map(x => x.id === id ? { ...x, ...patch } : x));
  }
  function dismissLog(id: string) {
    setUploadLog(prev => prev.filter(x => x.id !== id));
  }

  async function load() {
    // v0.61.5/6 ：用 allSettled — 单个 API fail（如 listExternalReports 后端
    // 抛错）不应该把整个 load 拽崩，导致 externals 永远 0 长 → 合并/下一步
    // 按钮神秘消失。listExternalReports 和 listIntegratedReports 不再 silent
    // catch，真错误会冒泡 — 这里 surface 给用户。
    const [lsR, rsR, psR, extsR, intsR] = await Promise.allSettled([
      api.libraries(), api.listInsights(), api.platforms(),
      api.listExternalReports(), api.listIntegratedReports(),
    ]);
    if (lsR.status === "fulfilled") {
      setLibs(lsR.value);
      const active = lsR.value.find(l => l.active);
      if (active) setSelectedLibId(active.lib_id);
      else if (lsR.value[0]) setSelectedLibId(lsR.value[0].lib_id);
    }
    if (rsR.status === "fulfilled") setReports(rsR.value as any);
    if (psR.status === "fulfilled") setPlatforms(psR.value);
    if (extsR.status === "fulfilled") {
      setExternals(extsR.value as any);
      // v0.61.6 ：默认全选 — 用户上传完就能直接点「整合」，
      // 不需要先记得手动勾选每一份（之前许多人以为按钮坏了，其实是 disabled）。
      setSelectedSourceIds(new Set(extsR.value.map((e: any) => e.report_id)));
    } else {
      // eslint-disable-next-line no-console
      console.error("[Reports] listExternalReports failed:", extsR.reason);
      setErr(`外部报告加载失败 ：${(extsR.reason as any)?.message ?? String(extsR.reason)}`);
    }
    if (intsR.status === "fulfilled") setIntegrated(intsR.value as any);
    else {
      // eslint-disable-next-line no-console
      console.error("[Reports] listIntegratedReports failed:", intsR.reason);
    }
  }
  useEffect(() => { load(); }, []);

  // ---- External report handlers -----------------------------------------
  function toggleSource(id: string) {
    setSelectedSourceIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function handleAnyFile(f: File | null) {
    if (!f) return;
    const logId = pushLog({ name: f.name, status: "uploading" });
    setErr(null); setInfo(null); setUploadBusy(true);
    try {
      const r = await api.uploadExternalReportFile(
        f, f.name.replace(/\.[^.]+$/, ""), selectedLibId || null
      );
      updateLog(logId, {
        status: "ok", name: r.name,
        chars: r.content_chars, format: r.format, warn: r.extract_warning,
      });
      await load();
    } catch (e: any) {
      updateLog(logId, { status: "fail", error: humaniseError(e) });
    } finally {
      setUploadBusy(false);
    }
  }

  async function submitUpload() {
    if (!uploadName.trim()) { setErr("给这份报告起个名字"); return; }
    if (!uploadContent.trim()) { setErr("报告内容不能为空"); return; }
    const logId = pushLog({ name: uploadName.trim(), status: "uploading" });
    setErr(null); setUploadBusy(true);
    try {
      const r = await api.uploadExternalReport({
        name: uploadName.trim(), content: uploadContent,
        library_id: selectedLibId || null,
        format: /^#|\*\*|^\s*[-*]\s/m.test(uploadContent) ? "markdown" : "text",
      });
      updateLog(logId, {
        status: "ok", chars: r.content_chars, format: r.format,
      });
      setUploadName(""); setUploadContent(""); setShowUpload(false);
      await load();
    } catch (e: any) {
      updateLog(logId, { status: "fail", error: humaniseError(e) });
    } finally {
      setUploadBusy(false);
    }
  }

  async function deleteExt(id: string) {
    try {
      await api.deleteExternalReport(id);
      setSelectedSourceIds(prev => {
        const next = new Set(prev); next.delete(id); return next;
      });
      await load();
    } catch (e: any) { setErr(humaniseError(e)); }
  }

  async function runIntegrate() {
    const ids = Array.from(selectedSourceIds);
    if (ids.length === 0) { setErr("勾一份以上的外部报告再整合"); return; }
    setErr(null); setInfo(null); setIntegrating(true);
    integrateAbortRef.current = new AbortController();
    try {
      const ownLatest = includeOwnConsensus
        ? reportsForSelected.find(r => r.status === "completed")?.report_id ?? null
        : null;
      const r = await api.integrateExternalReports({
        source_ids: ids,
        library_id: selectedLibId || null,
        include_consensus_report_id: ownLatest,
        model_spec: "openai:gpt-4o",
      }, integrateAbortRef.current.signal);
      setInfo(`✓ GPT-4o 整合完成（${r.elapsed_s}s）· 整合报告已生成。`);
      await load();
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 整合已暂停。点🚀整合所选可重新开始。");
      } else {
        setErr(humaniseError(e));
      }
    } finally {
      setIntegrating(false);
      integrateAbortRef.current = null;
    }
  }

  // ---- Combined upload → DNA → insight flow ------------------------------
  async function handleFile(f: File | null) {
    if (!f) return;
    if (!/\.(db|sqlite|sqlite3)$/i.test(f.name)) {
      setErr(`只接受 SQLite 数据库文件（.db / .sqlite / .sqlite3）：${f.name}`);
      return;
    }
    setErr(null); setInfo(null);
    const displayName = f.name.replace(/\.(db|sqlite|sqlite3)$/i, "");
    try {
      // Both backend calls (import + insight) are opaque, so we run an
      // auto-pacing progress timeline + fast-forward to done at the end.
      setStage("running-insight");  // single "we're working" state
      setProgress("");

      const imp = await api.importLibrary(f, displayName, pendingPlatform);

      // Silent on schema details — the AI has already adapted under the hood.
      // We just say "AI 把源表映射好了" if it was non-canonical, nothing else.
      if (imp.adapter?.adapted) {
        const m = imp.adapter.mapping_summary?.notes;
        if (m?.source_table) {
          setInfo(`🔄 AI 自动适配了你库里的「${m.source_table}」表`);
        }
      }

      insightAbortRef.current = new AbortController();
      const r = await api.runInsight(imp.lib_id, undefined, insightAbortRef.current.signal);
      setStage("done");
      setProgress("✓ 报告完成");
      setTimeout(() => navigate(`/reports/${r.report_id}`), 600);
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 已暂停（库已上传，可以稍后到「已有库 · 重新生成报告」重跑分析）。");
      } else {
        setErr(humaniseError(e));
      }
      setStage("idle");
      setProgress("");
    } finally {
      insightAbortRef.current = null;
    }
  }

  // ---- Re-analyze an existing library -----------------------------------
  async function reAnalyze() {
    if (!selectedLibId) { setErr("先选个数据库"); return; }
    setErr(null);
    try {
      if (!libs.find(l => l.lib_id === selectedLibId)?.active) {
        await api.activateLibrary(selectedLibId);
      }
      setStage("running-insight");
      setProgress("");
      insightAbortRef.current = new AbortController();
      const r = await api.runInsight(selectedLibId, undefined, insightAbortRef.current.signal);
      setStage("done");
      setTimeout(() => navigate(`/reports/${r.report_id}`), 600);
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 已暂停。点🚀重跑报告可以再来。");
      } else {
        setErr(humaniseError(e));
      }
      setStage("idle");
      setProgress("");
    } finally {
      insightAbortRef.current = null;
    }
  }

  const offline = !api.isConnected();
  const hasLib = libs.length > 0;
  const selected = libs.find(l => l.lib_id === selectedLibId);
  const reportsForSelected = reports.filter(r => r.library_id === selectedLibId);
  const busy = stage !== "idle" && stage !== "done";

  return (
    <div>
      <div className="page-header">
        <h1>📊 分析报告 · 第 1 步</h1>
        <p>{hasLib ? "双 AI 共识 + 图表" : "拖一个 .db 进来 → AI 出共识报告"}</p>
      </div>

      {err && (
        <div className="banner danger" style={{display: "flex",
                                                 justifyContent: "space-between",
                                                 alignItems: "flex-start", gap: 12}}>
          <div style={{whiteSpace: "pre-wrap", flex: 1}}>{err}</div>
          <button className="ghost" style={{padding: "4px 8px", fontSize: 12, flexShrink: 0}}
            onClick={() => setErr(null)}>关闭</button>
        </div>
      )}
      {info && !err && (
        <div className="banner info" onClick={() => setInfo(null)}>{info}</div>
      )}
      {offline && (
        <div className="banner warn">
          本地后端没起来。顶部黄条有启动命令；起来后回这里。
        </div>
      )}

      {/* ====== Combined hero dropzone (always visible, primary entry) ====== */}
      <div className="card" style={{
        background: hasLib ? "var(--bg-card)" : "linear-gradient(180deg, #fff7e6 0%, #fff 100%)",
        borderColor: hasLib ? undefined : "#fde2a3",
      }}>
        <h2 style={{marginTop: 0}}>{hasLib ? "🆕 上传新库 + 分析" : "🚀 第一次使用？拖一个 .db 上来"}</h2>

        <div
          className={`hero-drop ${dragOver ? "drag-over" : ""} ${busy ? "has-file" : ""}`}
          style={{ pointerEvents: busy || offline ? "none" : "auto",
                   opacity: busy ? 0.9 : (offline ? 0.5 : 1),
                   padding: hasLib ? "36px 24px" : "48px 24px" }}
          onDragOver={e => { e.preventDefault(); if (!busy) setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={e => { e.preventDefault(); setDragOver(false); handleFile(e.dataTransfer.files?.[0] ?? null); }}
          onClick={() => !busy && fileRef.current?.click()}
        >
          <input ref={fileRef} type="file" accept=".db,.sqlite,.sqlite3"
            style={{display: "none"}}
            onChange={e => handleFile(e.target.files?.[0] ?? null)} />
          {busy ? (
            <>
              <div className="big-icon">🤖🤖</div>
              <h2 style={{margin: 0}}>{progress || "AI 正在分析"}</h2>
              <div className="muted" style={{marginTop: 6}}>1-3 分钟，别关页面</div>
              <button className="ghost" onClick={(e) => { e.stopPropagation(); pauseInsight(); }}
                style={{pointerEvents: "auto", marginTop: 12, padding: "6px 16px", fontSize: 13}}>
                ⏸ 暂停
              </button>
            </>
          ) : (
            <>
              <div className="big-icon">📂</div>
              <h2 style={{margin: 0}}>把数据库丢给 AI</h2>
              <div className="muted" style={{marginTop: 6}}>
                .db / .sqlite / .sqlite3 · 任何 schema · 约 1-3 分钟
              </div>
            </>
          )}
        </div>

        {busy && (
          <ProgressTimeline
            stages={UPLOAD_STAGES}
            currentIndex={-1}
            auto
            done={false /* busy block 内 stage 永远不是 'done'，TS 也这么推断 */}
            error={err}
          />
        )}

        {!busy && (
          <div className="row" style={{justifyContent: "space-between", alignItems: "center", marginTop: 12}}>
            <div>
              <label style={{marginBottom: 4}}>来源平台</label>
              <select value={pendingPlatform} onChange={e => setPendingPlatform(e.target.value)}
                disabled={offline} style={{minWidth: 220, fontSize: 13}}>
                <option value="auto">🪄 自动嗅探（推荐）</option>
                {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div className="muted" style={{fontSize: 11, textAlign: "right", maxWidth: 360}}>
              没现成 .db？看 <a href={GITHUB_REPO + "#%E5%A4%9A%E5%B9%B3%E5%8F%B0--%E5%A4%9A-library"} target="_blank" rel="noreferrer">README 里的爬虫推荐</a>，
              或者去 <Link to="/settings#libraries">⚙️ 设置 → 资源库</Link> 看各平台的 schema 要求。
            </div>
          </div>
        )}
      </div>

      {/* ====== Re-analyze existing libraries ====== */}
      {hasLib && !busy && (
        <div className="card">
          <h2>📚 已有库 · 重新生成报告</h2>
          <div className="row" style={{gap: 12, alignItems: "flex-end", marginBottom: 4}}>
            <div style={{flex: 1}}>
              <label>选库</label>
              <select value={selectedLibId} onChange={e => setSelectedLibId(e.target.value)}
                disabled={offline} style={{width: "100%"}}>
                {libs.map(l => (
                  <option key={l.lib_id} value={l.lib_id}>
                    {l.active ? "★ " : ""}
                    {l.display_name} · {platformLabel(l.platform)} · {l.notes_count.toLocaleString()} 条
                  </option>
                ))}
              </select>
              {selected && (
                <div className="muted" style={{fontSize: 11, marginTop: 4}}>
                  <PlatformPill platform={selected.platform} /> ·
                  {selected.notes_count.toLocaleString()} 笔记 · {selected.comments_count.toLocaleString()} 评论 ·
                  {fmtBytes(selected.size_bytes)}
                </div>
              )}
            </div>
            <button onClick={reAnalyze} disabled={offline || !selectedLibId || busy}
              style={{minWidth: 160, fontSize: 14, padding: "10px 18px"}}>
              {busy ? "AI 工作中…" : "🚀 重跑报告"}
            </button>
            {busy && (
              <button className="ghost" onClick={pauseInsight}
                style={{minWidth: 80, fontSize: 14, padding: "10px 12px"}}>⏸ 暂停</button>
            )}
          </div>
          {busy && stage === "running-insight" && (
            <ProgressTimeline
              stages={REANALYZE_STAGES}
              currentIndex={-1}
              auto
              done={stage !== "running-insight"}
              error={err}
            />
          )}
        </div>
      )}

      {/* ====== User-uploaded external reports ====== */}
      <div className="card">
        <div>
          <h2 style={{margin: 0}}>📥 你自己的报告</h2>
          <p className="muted" style={{fontSize: 12, marginTop: 4, marginBottom: 12}}>
            咨询稿 / ChatGPT 分析 / 竞品拆解都行，AI 会自动引用。多份可整合。
          </p>
        </div>

        {/* Two upload modes side-by-side */}
        <div className="cards-grid" style={{gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 8}}>
          {/* File upload — accepts ANYTHING */}
          <div style={{padding: 14, background: "#fafafa", borderRadius: 8,
                       border: dragOver ? "2px dashed var(--primary)" : "2px dashed #ddd",
                       textAlign: "center", cursor: uploadBusy ? "wait" : "pointer",
                       opacity: uploadBusy ? 0.7 : 1}}
               onClick={() => !uploadBusy && textFileRef.current?.click()}
               onDragOver={e => { e.preventDefault(); if (!uploadBusy) setDragOver(true); }}
               onDragLeave={() => setDragOver(false)}
               onDrop={e => { e.preventDefault(); setDragOver(false);
                              handleAnyFile(e.dataTransfer.files?.[0] ?? null); }}>
            <div style={{fontSize: 32}}>📎</div>
            <div style={{fontWeight: 600, marginTop: 4}}>
              {uploadBusy ? "解析 + 上传中…" : "拖文件 / 点击选择"}
            </div>
            <div className="muted" style={{fontSize: 11.5, marginTop: 4}}>
              PDF / DOCX / MD / TXT / 任何文本
            </div>
            <input type="file" ref={textFileRef}
              style={{display: "none"}}
              onChange={e => handleAnyFile(e.target.files?.[0] ?? null)} />
          </div>

          {/* Paste text */}
          <div style={{padding: 14, background: "#fafafa", borderRadius: 8, border: "1px solid #eee"}}>
            <div style={{fontWeight: 600, marginBottom: 6}}>✍️ 或直接粘贴文本</div>
            <button onClick={() => { setShowUpload(s => !s); setUploadName(""); setUploadContent(""); }}
              disabled={offline} style={{width: "100%", fontSize: 13, padding: "8px 0"}}>
              {showUpload ? "✕ 收起" : "＋ 粘贴文本"}
            </button>
          </div>
        </div>

        {showUpload && (
          <div style={{marginTop: 14, padding: 12, background: "#fafafa", borderRadius: 8}}>
            <div style={{marginBottom: 8}}>
              <label>报告名 *</label>
              <input value={uploadName} onChange={e => setUploadName(e.target.value)}
                placeholder="比如：竞品 A 起号拆解 / 咨询稿 v2 / ChatGPT 分析" />
            </div>
            <div style={{marginBottom: 8}}>
              <label>报告内容 *（直接粘贴文本 / Markdown）</label>
              <textarea value={uploadContent}
                onChange={e => setUploadContent(e.target.value)}
                placeholder="粘贴这份报告的全文。支持 Markdown 排版；图表请用文字描述（『hook 类型分布：好物推荐 38% / 干货 22%…』）。"
                style={{minHeight: 240, fontFamily: "inherit", fontSize: 13, lineHeight: 1.7}} />
              <div className="muted" style={{fontSize: 11, marginTop: 3}}>
                字数 ：{uploadContent.length.toLocaleString()} · 越具体越好，AI 会按内容质量整合
              </div>
            </div>
            <div className="row" style={{gap: 8}}>
              <button onClick={submitUpload} disabled={uploadBusy}>
                {uploadBusy ? "上传中…" : "💾 保存这份报告"}
              </button>
              <button className="ghost" onClick={() => setShowUpload(false)}>关闭</button>
            </div>
          </div>
        )}

        {/* Live per-file upload log — shows immediately so the user sees what
            they uploaded + whether each succeeded. */}
        {uploadLog.length > 0 && (
          <div style={{marginTop: 14, padding: 12,
                       background: "#fafbff", borderRadius: 8,
                       border: "1px solid #e6e9f5"}}>
            <div className="row" style={{justifyContent: "space-between", alignItems: "baseline"}}>
              <b style={{fontSize: 13}}>📋 本次上传记录</b>
              <button className="ghost" onClick={() => setUploadLog([])}
                style={{fontSize: 11, padding: "2px 8px"}}>清空记录</button>
            </div>
            <div style={{marginTop: 8, display: "grid", gap: 6}}>
              {uploadLog.map(item => {
                const tone = item.status === "ok" ? {bg: "#ecfdf5", color: "#065f46", icon: "✓"}
                          : item.status === "fail" ? {bg: "#fef2f2", color: "#991b1b", icon: "✗"}
                          : {bg: "#fef3c7", color: "#92400e", icon: "⏳"};
                return (
                  <div key={item.id} style={{
                    display: "flex", justifyContent: "space-between",
                    alignItems: "flex-start", gap: 10,
                    padding: "8px 10px", background: tone.bg,
                    color: tone.color, borderRadius: 6, fontSize: 12.5,
                  }}>
                    <div style={{flex: 1, minWidth: 0}}>
                      <span style={{fontWeight: 700, marginRight: 6}}>{tone.icon}</span>
                      <span style={{fontWeight: 600}}>{item.name}</span>
                      {item.status === "uploading" && (
                        <span style={{marginLeft: 6, fontStyle: "italic"}}>解析 + 上传中…</span>
                      )}
                      {item.status === "ok" && (
                        <span className="muted" style={{marginLeft: 6, color: "inherit", opacity: 0.85}}>
                          · 已成功 · {item.chars?.toLocaleString() ?? "?"} 字 · {item.format}
                          {item.warn ? ` · ⚠️ ${item.warn}` : ""}
                        </span>
                      )}
                      {item.status === "fail" && item.error && (
                        <div style={{marginTop: 4, fontSize: 11.5, whiteSpace: "pre-wrap"}}>
                          {item.error}
                        </div>
                      )}
                    </div>
                    <button className="ghost" onClick={() => dismissLog(item.id)}
                      style={{fontSize: 11, padding: "0 6px", color: "inherit"}}>×</button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {externals.length > 0 && (
          <>
            <h3 style={{margin: "18px 0 4px", fontSize: 14}}>
              已保存（{externals.length}）
            </h3>
            <p className="muted" style={{margin: "0 0 6px", fontSize: 11.5}}>
              起号策略 / Composer 自动引用。
            </p>
            <table className="table" style={{marginTop: 6}}>
              <thead>
                <tr>
                  <th style={{width: 36}}>选</th>
                  <th>报告名</th>
                  <th className="num">字数</th>
                  <th>来源</th>
                  <th>时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {externals.map(e => (
                  <tr key={e.report_id}>
                    <td>
                      <input type="checkbox" checked={selectedSourceIds.has(e.report_id)}
                        onChange={() => toggleSource(e.report_id)} />
                    </td>
                    <td><b>{e.name}</b>
                      {e.library_id && (
                        <div className="muted" style={{fontSize: 11}}>
                          归属库 ：{libs.find(l => l.lib_id === e.library_id)?.display_name ?? e.library_id}
                        </div>
                      )}
                    </td>
                    <td className="num">{e.content_chars.toLocaleString()}</td>
                    <td className="muted" style={{fontSize: 12}}>{e.source} · {e.format}</td>
                    <td className="muted" style={{fontSize: 12}}>{fmtRelative(e.uploaded_at)}</td>
                    <td>
                      <button className="ghost" style={{fontSize: 11, padding: "2px 6px", color: "var(--danger)"}}
                        onClick={() => deleteExt(e.report_id)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {/* v0.61.9 ：整合卡永远渲染（哪怕 externals 为空），给用户「这功能存在」
            的可见 affordance。空状态按钮 disabled + 提示「先上传 1 份」。 */}
        <div style={{
          marginTop: 12, padding: 12,
          background: externals.length > 0 ? "var(--primary-soft)" : "#fafafa",
          borderRadius: 8,
          border: externals.length > 0 ? "1px solid var(--primary)" : "1px dashed #ddd",
          opacity: externals.length === 0 ? 0.85 : 1,
        }}>
          <div className="spread" style={{alignItems: "flex-start", gap: 10}}>
            <div style={{flex: 1}}>
              <b>🪄 整合所选 {selectedSourceIds.size} 份 → 一份共识报告</b>
              <div className="muted" style={{fontSize: 11.5, marginTop: 4}}>
                {externals.length === 0
                  ? "上传 ≥ 2 份外部报告后，让 GPT-4o 跨报告找共识 + 分歧 → 生成统一稿。"
                  : "跨报告找共识 + 分歧 → 一份统一稿，下游 Strategy / Composer 自动读最新整合稿。"}
              </div>
              {externals.length > 0 && (
                <label style={{display: "inline-flex", alignItems: "center", gap: 6,
                                fontSize: 12, marginTop: 8, cursor: "pointer"}}>
                  <input type="checkbox" checked={includeOwnConsensus}
                    onChange={e => setIncludeOwnConsensus(e.target.checked)} />
                  一起融合工具自出共识（如有）
                </label>
              )}
            </div>
            <div className="row" style={{gap: 6}}>
              <button onClick={runIntegrate}
                disabled={integrating || selectedSourceIds.size === 0 || offline}
                title={externals.length === 0 ? "先上传 ≥ 1 份外部报告" : ""}
                style={{minWidth: 160}}>
                {integrating ? "🤖 整合中…"
                : externals.length === 0 ? "⏳ 等你上传报告"
                : "🚀 整合所选"}
              </button>
              {integrating && (
                <button className="ghost" onClick={pauseIntegrate}
                  style={{padding: "8px 14px", fontSize: 13}}>⏸ 暂停</button>
              )}
            </div>
          </div>
        </div>

        {integrated.length > 0 && (
          <div style={{marginTop: 16}}>
            <h3 style={{margin: "0 0 6px"}}>📚 整合稿历史</h3>
            <table className="table">
              <thead>
                <tr><th>时间</th><th>整合了几份</th><th>状态</th><th>耗时</th><th></th></tr>
              </thead>
              <tbody>
                {integrated.slice(0, 10).map(ig => (
                  <tr key={ig.integrated_id}>
                    <td>{fmtTime(ig.created_at)}</td>
                    <td className="num">{ig.source_ids?.length ?? 0}</td>
                    <td>
                      {ig.status === "completed" ? <span style={{color: "var(--ok)"}}>✓ 完成</span>
                      : ig.status === "failed" ? <span style={{color: "var(--danger)"}}>✗ {ig.error?.slice(0, 60) ?? "失败"}</span>
                      : <span className="muted">{ig.status}</span>}
                    </td>
                    <td className="muted">{ig.elapsed_s ? `${ig.elapsed_s}s` : "—"}</td>
                    <td>
                      {ig.status === "completed" && (
                        <Link to={`/integrated/${ig.integrated_id}`}>查看整合稿 →</Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* "Next step" hand-off — only shown once user has at least one external
          report saved, since that's when the downstream features start
          benefiting from these uploads. */}
      {externals.length > 0 && (
        <NextStepCard
          label={`去 🚀 起号策略（引用 ${externals.length} 份报告）`}
          hint={
            integrated.length > 0
              ? `Strategy / Composer 自动读最新整合稿。`
              : `直接读原文也行；整合一下效果更好。`
          }
          to="/strategy"
          emoji="→"
        />
      )}

      {/* ====== Pipeline explainer ====== */}
      <details className="card" style={{background: "#fafafa"}}>
        <summary style={{cursor: "pointer", fontWeight: 600, fontSize: 14}}>
          ▾ 这份报告里会有什么 · AI 团队怎么工作的
        </summary>
        <div style={{padding: "10px 4px 4px", fontSize: 13, lineHeight: 1.7, color: "#555"}}>
          <p style={{margin: "6px 0"}}>
            <b>多 agent 协作 + 互相查漏补缺：</b>
          </p>
          <ol style={{marginLeft: 20, marginTop: 4}}>
            <li><b>Phase 1（独立分析）：</b>Claude Opus 和 OpenAI GPT-4o 各自看 DNA 数据，独立写一份报告</li>
            <li><b>Phase 2（互评查漏）：</b>双方读对方的报告 → 写「我赞成的 / 我反对的 / 对方漏了的」</li>
            <li><b>Phase 3（主编融合）：</b>主编（Claude Opus）只把<b>双方都认可</b>的点放进正式报告，分歧/单方观点单独列出</li>
          </ol>
          <p style={{margin: "8px 0 4px"}}><b>报告内容：</b></p>
          <ul style={{marginLeft: 20}}>
            <li>📌 总览 + 关键发现（每条引用 DNA 真实数字）</li>
            <li>🚀 内容机会（蓝海赛道 + 切入方式）</li>
            <li>⚠️ 风险与盲区</li>
            <li>📈 推荐下一步</li>
            <li>🗨️ 双方分歧 / 单方观点（透明保留，避免一家独大）</li>
            <li>📊 数据图表（蓝海排行 / hook 分布 / 时段热力图 / tags / 字数）</li>
          </ul>
        </div>
      </details>

      {/* ====== Historical reports ====== */}
      {hasLib && reportsForSelected.length > 0 && (
        <div className="card">
          <h2>该库的历史报告 ({reportsForSelected.length})</h2>
          <table className="table">
            <thead>
              <tr><th>时间</th><th>状态</th><th>耗时</th><th></th></tr>
            </thead>
            <tbody>
              {reportsForSelected.map(r => (
                <tr key={r.report_id}>
                  <td>{fmtTime(r.created_at)}</td>
                  <td>
                    {r.status === "completed" ? (
                      <span style={{color: "var(--ok)"}}>✓ 完成</span>
                    ) : r.status === "failed" ? (
                      <span style={{color: "var(--danger)"}}>✗ 失败</span>
                    ) : (
                      <span className="muted">{r.status}</span>
                    )}
                  </td>
                  <td className="muted">{r.elapsed_s ? `${r.elapsed_s}s` : "—"}</td>
                  <td>
                    {r.status === "completed" && (
                      <Link to={`/reports/${r.report_id}`}>查看报告 →</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reports.length > reportsForSelected.length && (
        <div className="card" style={{background: "#fafafa"}}>
          <h3 style={{margin: 0}}>其他库的报告</h3>
          <table className="table" style={{marginTop: 8}}>
            <thead><tr><th>库</th><th>时间</th><th></th></tr></thead>
            <tbody>
              {reports
                .filter(r => r.library_id !== selectedLibId)
                .slice(0, 10)
                .map(r => {
                  const lib = libs.find(l => l.lib_id === r.library_id);
                  return (
                    <tr key={r.report_id}>
                      <td>{lib?.display_name ?? r.library_id}</td>
                      <td className="muted">{fmtRelative(r.created_at)}</td>
                      <td>{r.status === "completed" && <Link to={`/reports/${r.report_id}`}>查看 →</Link>}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}

      {hasLib && reportsForSelected.length > 0 && (
        <NextStepCard
          label="去 🚀 起号策略"
          hint="基于共识报告自动拟方案（周历 + 选题 + 材料）"
          to="/strategy"
        />
      )}
    </div>
  );
}
