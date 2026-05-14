import { useEffect, useState } from "react";
import { api, backendUrl, DEFAULT_BACKEND_URL } from "../api";
import { GITHUB_REPO } from "../catalog";

const PWSH_CMD = `# 一次性 setup（首次跑这条）
git clone ${GITHUB_REPO}
cd xhsAccountRise
.\\start.ps1

# 之后每次启动只需:
.\\start.ps1`;

const BASH_CMD = `# 一次性 setup（首次跑这条）
git clone ${GITHUB_REPO}
cd xhsAccountRise
chmod +x start.sh && ./start.sh

# 之后每次启动只需:
./start.sh`;

export default function ConnectionBanner() {
  const [status, setStatus] = useState<"checking" | "ok" | "down">("checking");
  const [shell, setShell] = useState<"pwsh" | "bash">("pwsh");
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancel = false;
    async function tick() {
      if (document.hidden) return;
      const url = backendUrl();
      if (!url) { if (!cancel) setStatus("down"); return; }
      const h = await api.health();
      if (!cancel) setStatus(h.ok ? "ok" : "down");
    }
    tick();
    const t = setInterval(tick, 20_000);  // was 8s — too aggressive
    function onVisible() { if (!document.hidden) tick(); }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancel = true; clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (status !== "down") return null;

  const cmd = shell === "pwsh" ? PWSH_CMD : BASH_CMD;

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {/* ignore */}
  }

  return (
    <div className="conn-banner">
      <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start"}}>
        <div>
          <b>需要启动本地后端</b> ·
          本工具的 AI 调用 / 数据库上传 都在你本机跑（API key + 数据不离开你电脑）
        </div>
        <button className="ghost" style={{padding: "2px 8px", fontSize: 12}}
          onClick={() => setExpanded(!expanded)}>
          {expanded ? "▴ 收起" : "▾ 怎么启动"}
        </button>
      </div>

      {expanded && (
        <>
          <div className="row" style={{gap: 6, marginTop: 6, fontSize: 12}}>
            首次在新设备使用？看下方步骤；已经 setup 过？直接跑 <code className="kbd">.\start.ps1</code>（Windows）或 <code className="kbd">./start.sh</code>（Mac/Linux）。
          </div>

          <div className="row" style={{gap: 4, marginTop: 4}}>
            <button className={shell === "pwsh" ? "secondary" : "ghost"}
              onClick={() => setShell("pwsh")} style={{padding: "2px 10px", fontSize: 11}}>Windows (PowerShell)</button>
            <button className={shell === "bash" ? "secondary" : "ghost"}
              onClick={() => setShell("bash")} style={{padding: "2px 10px", fontSize: 11}}>Mac / Linux</button>
          </div>

          <pre style={{
            background: "#1f2937", color: "#f9fafb", padding: "10px 12px",
            borderRadius: 6, fontSize: 11.5, lineHeight: 1.6,
            overflow: "auto", margin: "4px 0", maxHeight: 200,
          }}>{cmd}</pre>

          <div className="row" style={{gap: 8, justifyContent: "space-between", flexWrap: "wrap"}}>
            <div className="muted" style={{fontSize: 11}}>
              首次运行会自动建 venv、装 deps、提示填 .env (3 个 API key)。完事浏览器自动打开。
            </div>
            <div className="row" style={{gap: 6}}>
              <a href={GITHUB_REPO} target="_blank" rel="noreferrer"
                style={{fontSize: 11.5, padding: "2px 10px"}}>📦 仓库主页</a>
              <button className="secondary" onClick={copy} style={{padding: "2px 12px", fontSize: 12}}>
                {copied ? "✓ 已复制" : "复制命令"}
              </button>
            </div>
          </div>
        </>
      )}

      <div className="muted" style={{fontSize: 11, marginTop: 4}}>
        本卡片会在后端连通后自动消失。后端默认 <code className="kbd">{backendUrl() || DEFAULT_BACKEND_URL}</code>，要改去 ⚙️ 设置。
      </div>
    </div>
  );
}
