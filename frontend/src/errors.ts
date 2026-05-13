// Centralised error humaniser. Strips HTTP/status noise + suggests action.

export function humaniseError(e: unknown): string {
  const raw = (e instanceof Error ? e.message : String(e ?? "")) || "未知错误";

  // CORS / network / backend down — most common since the frontend is on
  // Pages (HTTPS) and the backend lives on localhost.
  if (/Failed to fetch|NetworkError|TypeError.*fetch|net::ERR/.test(raw)) {
    return (
      "和本地后端连不通。请确认：\n" +
      "  1. 后端已经启动 (.\\start.ps1 或 ./start.sh)\n" +
      "  2. 浏览器允许从 HTTPS 站点访问 http://localhost（Chrome 默认放行）\n" +
      "  3. 没有防火墙拦截 8765 端口"
    );
  }

  // Backend timeout
  if (/timeout|abort/i.test(raw)) {
    return "AI 调用超时，可能模型那边在排队。稍等再点一次。";
  }

  // Quota / billing
  if (/Insufficient Balance|quota|rate.?limit|429/i.test(raw)) {
    return "API 配额不够（DeepSeek 或 OpenAI 账户余额不足 / 触发限速）。去对应平台充值或换 LLM 配置。";
  }
  if (/401|403|invalid.*api.*key/i.test(raw)) {
    return "API key 不对或失效。检查后端 .env 里的 ANTHROPIC_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY 并重启后端。";
  }

  // 4xx with JSON body
  const httpMatch = raw.match(/(\d{3})[^:]*:\s*(.+)/);
  if (httpMatch) {
    const status = httpMatch[1];
    const body = httpMatch[2];
    // Try to pull "detail" from FastAPI JSON
    try {
      const j = JSON.parse(body);
      if (j?.detail) {
        if (status === "422") return `📄 文件被拒：${j.detail}`;
        if (status === "404") return `没找到对象：${j.detail}`;
        if (status === "409") return j.detail;
        return j.detail;
      }
    } catch { /* fall through */ }
    if (status === "422") return `📄 文件格式问题：${body.slice(0, 200)}`;
  }

  // Default — trim runaway long messages
  return raw.length > 400 ? raw.slice(0, 400) + "…" : raw;
}
