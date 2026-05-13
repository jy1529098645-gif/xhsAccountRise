import { useEffect, useState } from "react";
import { api, backendUrl, DEFAULT_BACKEND_URL } from "../api";

const CMD = '$env:PYTHONUTF8="1"; $env:PYTHONPATH="$(Get-Location)"; .venv\\Scripts\\python -m studio serve --port 8765';

export default function ConnectionBanner() {
  const [status, setStatus] = useState<"checking" | "ok" | "down">("checking");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancel = false;
    async function tick() {
      const url = backendUrl();
      if (!url) { if (!cancel) setStatus("down"); return; }
      const h = await api.health();
      if (!cancel) setStatus(h.ok ? "ok" : "down");
    }
    tick();
    const t = setInterval(tick, 8000);
    return () => { cancel = true; clearInterval(t); };
  }, []);

  if (status !== "down") return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {/* ignore */}
  }

  return (
    <div className="conn-banner">
      <div>
        <b>本地后端没起来</b> · 完整功能需要后端运行在 <code className="kbd">{backendUrl() || DEFAULT_BACKEND_URL}</code>
      </div>
      <div className="conn-banner-cmd">
        <code className="kbd cmd">{CMD}</code>
        <button className="ghost" onClick={copy} style={{padding: "2px 10px"}}>{copied ? "✓ 已复制" : "复制"}</button>
      </div>
      <div className="muted" style={{fontSize: 11}}>
        在仓库根目录 (<code className="kbd">H:\xhsAccountRise</code>) 打开 PowerShell 粘进去回车 → 看到 <code className="kbd">Uvicorn running</code> 就好。本卡片会自动消失。
      </div>
    </div>
  );
}
