import { useEffect, useState } from "react";
import { api, isAborted } from "../api";
import { humaniseErrorAsync } from "../errors";
import type { Platform } from "../types";
import { LLM_CATALOG } from "../catalog";

// Voice styles mirror the backend's VOICE_STYLES dict. Keep the labels in
// sync — `自定义` is special-cased to surface a free-form text input.
const VOICE_STYLES: { id: string; label: string; hint: string }[] = [
  { id: "口语自然", label: "口语自然", hint: "朋友聊天感、emoji 自然节奏，第一人称" },
  { id: "干货输出", label: "干货输出", hint: "信息密度高、行动可执行、句式简洁" },
  { id: "段子玩梗", label: "段子玩梗", hint: "情绪化 + emoji 高密度 + 自嘲反差，梗从报告里学" },
  { id: "走心叙事", label: "走心叙事", hint: "第一人称、画面感、情绪起伏" },
  { id: "学术严谨", label: "学术严谨", hint: "理性陈述、术语准确、引用具体" },
  { id: "种草热情", label: "种草热情", hint: "「闭眼入 / 真心推 / 平价宝藏」语气" },
  { id: "自定义", label: "自定义…", hint: "下面文本框里写你想要的语气" },
];

// localStorage key — form survives page navigation so a long quick-gen
// doesn't blow away the user's inputs.
const FORM_KEY = "studio.quickgen.form.v1";

interface FormState {
  title: string;
  platform: string;
  voice_style: string;
  voice_custom: string;
  target_length: number;
  extra: string;
  model_spec: string;
}

const DEFAULT_FORM: FormState = {
  title: "",
  platform: "",
  voice_style: "口语自然",
  voice_custom: "",
  target_length: 500,
  extra: "",
  // v0.62.20 ：显式钉 GPT-5 — 最新旗舰，质量上界，用户可改成 mini / nano
  // 省钱或换 claude:opus。bare "openai" 也能用（resolves to env default），
  // 但 UI 选中的 chip 模糊 — 用 "openai:gpt-5" 更明确。
  model_spec: "openai:gpt-5",
};

function loadForm(): FormState {
  try {
    const raw = localStorage.getItem(FORM_KEY);
    if (!raw) return DEFAULT_FORM;
    return { ...DEFAULT_FORM, ...JSON.parse(raw) };
  } catch { return DEFAULT_FORM; }
}

interface ResultState {
  title: string;
  body: string;
  tags: string[];
  cover_prompt: string;
  model_used: string;
  elapsed_s: number;
  cost_estimate_usd: number;
  used_report_context: boolean;
}

export default function QuickGenerate() {
  const [form, setForm] = useState<FormState>(loadForm);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<ResultState | null>(null);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch(e => console.error("[QuickGenerate] platforms", e));
    // Default to active library's platform if not set yet.
    if (!form.platform) {
      api.libraries().then(libs => {
        const active = libs.find((l: any) => l.active);
        if (active?.platform && !form.platform) {
          setForm(prev => ({ ...prev, platform: active.platform }));
        }
      }).catch(e => console.error("[QuickGenerate] libraries", e));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    try { localStorage.setItem(FORM_KEY, JSON.stringify(form)); }
    catch { /* quota — ignore */ }
  }, [form]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm(prev => ({ ...prev, [key]: value }));
  }

  async function run() {
    setErr(null); setResult(null); setRunning(true);
    try {
      const res = await api.quickGenerate({
        title: form.title.trim(),
        platform: form.platform,
        voice_style: form.voice_style,
        voice_custom: form.voice_style === "自定义" ? form.voice_custom : "",
        target_length: Number(form.target_length) || 500,
        extra: form.extra.trim(),
        model_spec: form.model_spec,
      });
      setResult({
        title: res.title,
        body: res.body,
        tags: res.tags,
        cover_prompt: res.cover_prompt,
        model_used: res.model_used,
        elapsed_s: res.elapsed_s,
        cost_estimate_usd: res.cost_estimate_usd,
        used_report_context: res.used_report_context,
      });
    } catch (e: any) {
      if (!isAborted(e)) setErr(await humaniseErrorAsync(e));
    } finally {
      setRunning(false);
    }
  }

  function copyToClipboard(text: string) {
    try {
      navigator.clipboard.writeText(text);
    } catch { /* permission denied — user can select manually */ }
  }

  const noBackend = !api.isConnected();
  const canRun = !running && !noBackend
    && form.title.trim().length > 0
    && form.platform.length > 0;

  return (
    <div>
      <div className="page-header">
        <h1>⚡ 快速生成</h1>
        <p>填标题 → 选模型 → 一键出稿。单 LLM 调用，AI 会基于你上传的报告写。</p>
      </div>

      {noBackend && (
        <div className="banner warn">
          ⚠️ 本地后端没起来 — 快速生成需要后端调 LLM。
        </div>
      )}

      <div className="card">
        <h2 style={{margin: "0 0 14px"}}>1. 这一篇要写什么？</h2>

        <div style={{marginBottom: 10}}>
          <label>标题（这一篇的主线 — AI 必须围绕它写）</label>
          <input
            value={form.title}
            onChange={e => set("title", e.target.value)}
            placeholder="比如：留学党用 AI 写论文不被查重的 3 个套路"
          />
        </div>

        <div className="row" style={{gap: 12}}>
          <div style={{flex: 1, marginBottom: 10}}>
            <label>目标平台</label>
            <select value={form.platform} onChange={e => set("platform", e.target.value)}>
              <option value="">▾ 选一个平台</option>
              {platforms.map(p => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>
          <div style={{flex: 1, marginBottom: 10}}>
            <label>正文字数</label>
            <input
              type="number" min={80} max={4000} step={50}
              value={form.target_length}
              onChange={e => set("target_length", Number(e.target.value) || 500)}
            />
          </div>
        </div>

        <div style={{marginBottom: 10}}>
          <label>语言风格</label>
          <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4}}>
            {VOICE_STYLES.map(v => {
              const on = form.voice_style === v.id;
              return (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => set("voice_style", v.id)}
                  title={v.hint}
                  style={{
                    padding: "5px 12px", borderRadius: 16, fontSize: 13,
                    border: on ? "1.5px solid var(--primary)" : "1px solid var(--border)",
                    background: on ? "var(--primary-soft)" : "#fff",
                    color: on ? "var(--primary)" : "#333",
                    cursor: "pointer", fontWeight: on ? 600 : 400,
                  }}
                >
                  {on ? "✓ " : ""}{v.label}
                </button>
              );
            })}
          </div>
          {form.voice_style === "自定义" ? (
            <input
              style={{marginTop: 6}}
              value={form.voice_custom}
              onChange={e => set("voice_custom", e.target.value)}
              placeholder='比如：高冷克制、第二人称、不用 emoji'
            />
          ) : (
            <div className="muted" style={{fontSize: 11.5, marginTop: 4}}>
              {VOICE_STYLES.find(v => v.id === form.voice_style)?.hint}
            </div>
          )}
        </div>

        <div style={{marginBottom: 10}}>
          <label>附加要求（可选）</label>
          <textarea
            value={form.extra}
            onChange={e => set("extra", e.target.value)}
            placeholder='比如："不要露出 ChatGPT 字样" / "必须包含一个具体降重案例" / "结尾求私信"'
            style={{minHeight: 70}}
          />
        </div>

        <div style={{marginBottom: 10}}>
          <label>用哪个 AI 模型？</label>
          <select value={form.model_spec} onChange={e => set("model_spec", e.target.value)}>
            {LLM_CATALOG.map(m => (
              <option key={m.id} value={m.id}>
                {m.label} · {m.hint}
              </option>
            ))}
          </select>
          <div className="muted" style={{fontSize: 11, marginTop: 4}}>
            单个模型直接出稿（不走多 Agent 协作）。想要更高质量可选 Claude Opus；想最便宜选 DeepSeek。
          </div>
        </div>

        <div className="row" style={{gap: 8, marginTop: 16}}>
          <button onClick={run} disabled={!canRun}
            style={{flex: 1, fontSize: 15, padding: "10px 0"}}>
            {running ? "🤖 AI 正在写...(15-40s)" : "🚀 生成"}
          </button>
        </div>

        {err && (
          <div className="banner danger" style={{marginTop: 10}}>
            <div style={{whiteSpace: "pre-wrap"}}>{err}</div>
          </div>
        )}
      </div>

      {result && (
        <div className="card" style={{borderLeft: "3px solid var(--primary)"}}>
          <div className="spread" style={{alignItems: "flex-start", marginBottom: 10}}>
            <div>
              <h2 style={{margin: 0}}>📝 生成结果</h2>
              <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
                {result.model_used} · {result.elapsed_s.toFixed(1)}s ·
                ${result.cost_estimate_usd.toFixed(4)} ·
                {result.used_report_context ? " 用了报告上下文 ✓" : " ⚠️ 没有报告上下文"}
              </p>
            </div>
            <button className="secondary"
              onClick={() => copyToClipboard(`${result.title}\n\n${result.body}\n\n${result.tags.map(t => `#${t}`).join(" ")}`)}>
              📋 复制全文
            </button>
          </div>

          <div style={{marginBottom: 12}}>
            <div className="muted" style={{fontSize: 11.5, fontWeight: 600, marginBottom: 4}}>标题</div>
            <div style={{fontSize: 16, fontWeight: 600}}>{result.title}</div>
          </div>

          <div style={{marginBottom: 12}}>
            <div className="muted" style={{fontSize: 11.5, fontWeight: 600, marginBottom: 4}}>
              正文（{result.body.length} 字）
            </div>
            <div style={{
              padding: "12px 14px", background: "#fafafa", borderRadius: 6,
              fontSize: 14, lineHeight: 1.75, whiteSpace: "pre-wrap",
              borderLeft: "3px solid var(--primary)",
            }}>{result.body}</div>
          </div>

          {result.tags.length > 0 && (
            <div style={{marginBottom: 12}}>
              <div className="muted" style={{fontSize: 11.5, fontWeight: 600, marginBottom: 4}}>Tags</div>
              <div style={{display: "flex", flexWrap: "wrap", gap: 6}}>
                {result.tags.map((t, i) => (
                  <span key={i} className="tag-pill">#{t}</span>
                ))}
              </div>
            </div>
          )}

          {result.cover_prompt && (
            <details>
              <summary style={{cursor: "pointer", fontSize: 12.5, color: "var(--muted)"}}>
                ▾ 封面图 prompt（英文）
              </summary>
              <div style={{
                marginTop: 6, padding: "10px 12px", background: "#fafafa",
                borderRadius: 6, fontSize: 12, fontFamily: "monospace",
                whiteSpace: "pre-wrap",
              }}>{result.cover_prompt}</div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
