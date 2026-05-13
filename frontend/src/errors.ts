// Centralised error humaniser. Strips HTTP/status noise + suggests action.

import { api, backendUrl } from "./api";

// In-memory health cache so we don't re-probe on every error.
let _lastProbe: { ts: number; ok: boolean } | null = null;

async function backendIsAlive(): Promise<boolean> {
  if (!backendUrl()) return false;
  const now = Date.now();
  if (_lastProbe && now - _lastProbe.ts < 4000) return _lastProbe.ok;
  try {
    const h = await api.health();
    _lastProbe = { ts: now, ok: !!h.ok };
    return h.ok;
  } catch {
    _lastProbe = { ts: now, ok: false };
    return false;
  }
}

/**
 * Async variant — does a quick /health probe before claiming the backend
 * is down. Long-running calls (Strategy expand / Composer 60-180s) often
 * fail with "Failed to fetch" mid-flight even though the backend is fine,
 * and the misleading "后端没启动" message has burned the user twice now.
 */
export async function humaniseErrorAsync(e: unknown): Promise<string> {
  const raw = (e instanceof Error ? e.message : String(e ?? "")) || "未知错误";
  if (/Failed to fetch|NetworkError|TypeError.*fetch|net::ERR/.test(raw)) {
    const alive = await backendIsAlive();
    if (alive) {
      return (
        "AI 调用中断了 — 后端还在跑，但浏览器和后端之间的连接断了。\n" +
        "常见原因 ：长时间请求被网络中断 / 切了 Wi-Fi / 电脑休眠 / 后端那边正好重启。\n" +
        "→ 稍等 10s 直接点 ↻ 重试。"
      );
    }
    return (
      "和本地后端连不通。请确认：\n" +
      "  1. 后端已经启动 (.\\start.ps1 或 ./start.sh)\n" +
      "  2. 浏览器允许从 HTTPS 站点访问 http://localhost（Chrome 默认放行）\n" +
      "  3. 没有防火墙拦截 8765 端口"
    );
  }
  return humaniseError(e);
}

export function humaniseError(e: unknown): string {
  const raw = (e instanceof Error ? e.message : String(e ?? "")) || "未知错误";

  // CORS / network / backend down. NOTE: prefer humaniseErrorAsync when you
  // can — it probes /health and softens the message if the backend is OK.
  if (/Failed to fetch|NetworkError|TypeError.*fetch|net::ERR/.test(raw)) {
    return (
      "AI 调用中断了。可能是 ：\n" +
      "  · 长时间请求被网络断开（休眠 / 切网络 / 后端重启）— 稍等再点 ↻ 重试\n" +
      "  · 本地后端没起来 — 看顶部黄条命令启动\n" +
      "  · 浏览器拦截了 HTTPS→localhost — Chrome 通常默认允许"
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
