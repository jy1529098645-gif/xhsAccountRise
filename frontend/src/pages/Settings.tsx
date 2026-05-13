import { useEffect, useState } from "react";
import { api, backendUrl, setBackendUrl } from "../api";

const DEFAULT = "http://127.0.0.1:8765";

export default function Settings() {
  const [url, setUrl] = useState(backendUrl());
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);

  async function check() {
    setChecking(true);
    const ok = await api.health();
    setHealthy(ok.ok);
    setChecking(false);
  }
  useEffect(() => { if (url) check(); }, []);

  function save(newUrl: string) {
    setBackendUrl(newUrl);
    setUrl(newUrl);
    setHealthy(null);
    if (newUrl) check();
  }

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
        <p>配置本地后端连接、查看 LLM keys 状态、首次使用引导。</p>
      </div>

      <div className="card">
        <h2>Backend URL</h2>
        <p className="muted">连接本地 FastAPI 后端以启用完整功能（上传库 / 生成稿件 / 评分 / 选择 final）。留空则进入静态演示模式。</p>
        <div className="row">
          <input style={{flex: 1}} value={url} onChange={e => setUrl(e.target.value)}
            placeholder={DEFAULT} />
          <button className="secondary" onClick={() => save(DEFAULT)}>填默认</button>
          <button onClick={() => save(url.trim())}>保存</button>
          {url && <button className="ghost" onClick={() => save("")}>清除</button>}
        </div>
        <div style={{marginTop: 10}}>
          {!url ? (
            <span className="muted">未连接 · 静态模式</span>
          ) : checking ? (
            <span className="muted">检测中…</span>
          ) : healthy ? (
            <span style={{color: "var(--ok)"}}>✓ 后端在 {url} 上正常响应</span>
          ) : healthy === false ? (
            <span style={{color: "var(--danger)"}}>✗ 无法访问 {url}</span>
          ) : null}
        </div>
      </div>

      <div className="card">
        <h2>本地后端启动指南</h2>
        <ol style={{marginLeft: 20, lineHeight: 1.9}}>
          <li>克隆仓库：<code className="kbd">git clone https://github.com/jy1529098645-gif/xhsAccountRise.git</code></li>
          <li>装 Python deps：<code className="kbd">python -m venv .venv && .venv\Scripts\pip install -r requirements.txt</code></li>
          <li>复制 <code className="kbd">.env.example</code> 到 <code className="kbd">.env</code>，填三家 API key</li>
          <li>初始化：<code className="kbd">python -m studio migrate && python -m studio rag build</code></li>
          <li>跑分析：<code className="kbd">python -m studio analyze && python -m studio promote-hooks</code></li>
          <li>启服务：<code className="kbd">python -m studio serve --port 8765</code></li>
          <li>回这里把 Backend URL 填 <code className="kbd">http://127.0.0.1:8765</code></li>
        </ol>
      </div>

      <div className="card">
        <h2>LLM 配置</h2>
        <p className="muted">所有 keys 通过本地 .env 注入到后端进程，前端永远看不到也不会传输。</p>
        <ul style={{lineHeight: 1.9}}>
          <li><b>Anthropic Claude</b>：<code className="kbd">ANTHROPIC_API_KEY=sk-ant-...</code> — 推荐主力（Strategist + Refiner + Critic）</li>
          <li><b>DeepSeek</b>：<code className="kbd">DEEPSEEK_API_KEY=sk-...</code> — 中文下沉感强，适合 Drafter / Critic</li>
          <li><b>OpenAI</b>：<code className="kbd">OPENAI_API_KEY=sk-...</code> + <code className="kbd">OPENAI_MODEL=gpt-5</code> — Drafter 多样性</li>
        </ul>
        <p className="muted">在 Composer 里可以为每个 Agent role 指定 LLM 组合。</p>
      </div>

      <div className="card">
        <h2>多 Agent 架构</h2>
        <p style={{lineHeight: 1.8}}>
          这套 pipeline 设计追求「最贴近真实创作流程」而非「N 个 LLM 投票」：
        </p>
        <ol style={{marginLeft: 20, lineHeight: 1.9}}>
          <li><b>Strategist</b>（1 个强 LLM）—— 先看 brief 和 RAG 参考，定 hook 类型、开头钩子、结构、语气、避坑。一锤定音，避免下游 drafter 各自为政。</li>
          <li><b>Researcher</b>（无 LLM）—— FTS5 检索 top 参考爆款 + 用户原话评论 + 高表现 hook 模板，喂给下游所有 agent。</li>
          <li><b>Drafter Pool</b>（N 个不同 LLM 并发）—— 同一 brief 各家出一稿。Claude 严谨，DeepSeek 下沉感强，GPT 多样性。</li>
          <li><b>Critic Pool</b>（M 个不同 LLM 并发）—— 给每份候选打 5 维分（hook / 语言贴合 / 转发欲望 / 品牌安全 / 结构）。跨 LLM 评分降低自夸偏差。</li>
          <li><b>Refiner</b>（1 个强 LLM）—— 拿到 critic 共识 top 候选，按具体建议改稿。不换 hook 类型，只修缺陷。</li>
          <li><b>Synthesizer</b>（无 LLM）—— 选 final（默认拿 Refiner 改后的版本）。</li>
        </ol>
        <p className="muted">每一步在 Composer 的「Agent 时间线」中都可以看到 latency / cost / 输入输出 summary，全程透明。</p>
      </div>
    </div>
  );
}
