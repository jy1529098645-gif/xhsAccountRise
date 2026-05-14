import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, backendUrl, setBackendUrl, resetBackendUrl, DEFAULT_BACKEND_URL } from "../api";
import { GITHUB_REPO } from "../catalog";
import { LibrariesSection } from "./Libraries";

const DEFAULT = DEFAULT_BACKEND_URL;

export default function Settings() {
  const [url, setUrl] = useState(backendUrl());
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);
  const location = useLocation();

  async function check() {
    setChecking(true);
    const ok = await api.health();
    setHealthy(ok.ok);
    setChecking(false);
  }
  useEffect(() => { if (url) check(); }, []);

  // v0.54: scroll to the Libraries section when navigated to with the
  // #libraries anchor (from Reports.tsx and Dashboard.tsx links). Run on
  // every hash change; small delay so the embedded section has mounted.
  useEffect(() => {
    if (location.hash !== "#libraries") return;
    const t = setTimeout(() => {
      document.getElementById("libraries")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
    return () => clearTimeout(t);
  }, [location.hash]);

  function save(newUrl: string) {
    setBackendUrl(newUrl);
    setUrl(newUrl);
    setHealthy(null);
    if (newUrl) check();
  }

  return (
    <div>
      <div className="page-header">
        <h1>⚙️ 设置</h1>
        <p>资源库管理 + 后端连接 + LLM keys + 架构说明。</p>
      </div>

      {/* v0.54: Libraries used to be a top-level sidebar page; folded in
          here so the sidebar stays focused on the 4-step起号 flow. */}
      <div id="libraries" style={{scrollMarginTop: 20}}>
        <LibrariesSection />
      </div>

      <hr style={{margin: "24px 0", border: "none", borderTop: "1px solid #eee"}} />

      <div className="card">
        <h2>🚀 在新设备 / 服务器上启动后端</h2>
        <p style={{fontSize: 13}}>
          整个工具的源码都在 GitHub 仓库里，任何能装 Python 3.11+ 的设备都能跑：
        </p>
        <ol style={{marginLeft: 20, lineHeight: 1.9, fontSize: 13}}>
          <li>克隆仓库：<code className="kbd">git clone {GITHUB_REPO}.git</code></li>
          <li>进目录：<code className="kbd">cd xhsAccountRise</code></li>
          <li>Windows：双击 <code className="kbd">start.ps1</code>（或 <code className="kbd">.\start.ps1</code>）</li>
          <li>Mac/Linux：<code className="kbd">chmod +x start.sh && ./start.sh</code></li>
          <li>首次运行会自动建 venv + 装依赖 + 让你填 .env 里 3 个 API key</li>
          <li>看到 <code className="kbd">Uvicorn running on http://127.0.0.1:8765</code> 就好 → 浏览器会自动打开本前端 → 自动连上</li>
        </ol>
        <div style={{textAlign: "right", marginTop: 8}}>
          <a href={GITHUB_REPO} target="_blank" rel="noreferrer">📦 打开 GitHub 仓库 →</a>
        </div>
      </div>

      <div className="card">
        <h2>Backend URL</h2>
        <p className="muted">
          默认指向 <code className="kbd">{DEFAULT}</code> —— 通常不用改。
          只在你把后端跑在别的端口、别的机器、或者云服务器上时才需要改。
        </p>
        <div className="row">
          <input style={{flex: 1}} value={url} onChange={e => setUrl(e.target.value)}
            placeholder={DEFAULT} />
          <button className="secondary" onClick={() => { resetBackendUrl(); setUrl(""); check(); }}>恢复默认</button>
          <button onClick={() => save(url.trim())}>保存</button>
          {url && url !== DEFAULT && <button className="ghost" onClick={() => save("")}>关闭（用纯静态演示）</button>}
        </div>
        <div style={{marginTop: 10}}>
          {!url ? (
            <span className="muted">未连接 · 静态模式</span>
          ) : checking ? (
            <span className="muted">检测中…</span>
          ) : healthy ? (
            <span style={{color: "var(--ok)"}}>✓ 后端在 {url} 正常响应</span>
          ) : healthy === false ? (
            <span style={{color: "var(--danger)"}}>✗ 无法访问 {url}（可能没启动 / 端口冲突 / 防火墙）</span>
          ) : null}
        </div>
      </div>

      <div className="card">
        <h2>🤖 关于 LLM keys</h2>
        <p className="muted">所有 API key 通过本地 .env 文件注入后端进程。前端永远不会看到 / 不会传输 / 不会上云。</p>
        <ul style={{lineHeight: 1.9, fontSize: 13}}>
          <li><b>Anthropic Claude</b>：<code className="kbd">ANTHROPIC_API_KEY=sk-ant-...</code> —— 推荐主力（策略师 / 改稿师 / 融合师 默认它）</li>
          <li><b>DeepSeek</b>：<code className="kbd">DEEPSEEK_API_KEY=sk-...</code> —— 中文下沉感强，价格最便宜</li>
          <li><b>OpenAI</b>：<code className="kbd">OPENAI_API_KEY=sk-...</code> + 默认 <code className="kbd">OPENAI_MODEL=gpt-4o</code></li>
        </ul>
        <p className="muted" style={{fontSize: 12, marginTop: 8}}>
          想换模型变体（比如 OpenAI 切到 gpt-5 / o1）在 .env 改 <code className="kbd">OPENAI_MODEL</code>。重启后端生效。
        </p>
      </div>

      <div className="card">
        <h2>🧩 多 Agent 协作架构（在 Composer 里可自定义）</h2>
        <p style={{lineHeight: 1.8, fontSize: 13}}>
          这套 pipeline 不是"N 个 AI 投票"——是真实创作流程的拟态：
        </p>
        <ol style={{marginLeft: 20, lineHeight: 1.9, fontSize: 13}}>
          <li><b>策略师 Strategist</b>（1 个）—— 看 brief + RAG 参考，定 hook 类型、开头钩子、结构、语气、避坑。一锤定音。</li>
          <li><b>调研员 Researcher</b>（无 LLM）—— FTS5 检索 top 参考爆款 + 用户原话评论 + hook 模板。</li>
          <li><b>起草团 Drafter Pool</b>（N 个不同家并发）—— 同一 brief 各家出一稿。Claude 严谨 / DeepSeek 下沉 / GPT 多样。</li>
          <li><b>审稿团 Critic Pool</b>（M 个跨家）—— 5 维分（hook / 语言 / 转发 / 品牌 / 结构）。挑跟起草不同家来审，避免自夸。</li>
          <li><b>改稿师 Refiner</b>（1 个）—— 拿评分最高 + critic 反馈 → 改稿。</li>
          <li><b>融合师 Synthesizer</b>（1 个，★ 核心）—— 看完所有候选 + 评审 + 改稿 → 综合各家优点写最终融合稿。输出 rationale 标明从哪家取什么。</li>
          <li><b>计划师 Planner</b>（1 个）—— 给发布时段（基于历史 DNA 热力图）+ 后续 3-5 个选题 + 互动运营建议。</li>
        </ol>
        <p className="muted">每步 latency / 成本 / 错误状态都在 Composer 时间线可见。</p>
      </div>
    </div>
  );
}
