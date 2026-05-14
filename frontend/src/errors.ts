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

  // v0.62.7 ：把「余额不足」和「限速」分开 — 之前合在一条提示，用户
  // 充了钱也看到「去充值」，把真问题（限速）掩盖了。

  // DeepSeek 402 余额不足（错误体包含 "Insufficient Balance"）
  if (/Insufficient Balance|insufficient_balance|余额不足/i.test(raw)) {
    return "💰 DeepSeek 余额不足 — 去 platform.deepseek.com 充值。\n（如果你刚充值，等 1-2 分钟让账户同步再重试。）";
  }

  // OpenAI 账号 quota 用完（即使开了自动续费，monthly hard cap 仍可能触发）
  if (/insufficient_quota|exceeded.*quota|You exceeded your current quota/i.test(raw)) {
    return "💰 OpenAI quota 用完 — 检查 platform.openai.com/usage：\n  · 月度 hard limit 撞顶 → 提额\n  · payment method 失败 → 改卡\n  · 自动续费没触发 → 手动充";
  }

  // 限速（429 + rate-limit 关键词）— 多 agent 并发常见，不是钱的问题
  if (/rate.?limit|rate_limit_exceeded|Too Many Requests|429/i.test(raw)) {
    return "⏳ API 限速触发（RPM 或 TPM 超了，不是余额）。\n常见原因 ：起号策略 / 出稿同时并发了 3-5 家 LLM 把请求打爆。\n→ 等 30-60s 直接 ↻ 重试；或在「设置」里把并发模型砍到 1-2 家。";
  }

  // 模糊的 quota 提示（找不到具体类型时兜底）
  if (/quota/i.test(raw)) {
    return "API quota 报错（具体类型分不清）。原始信息 ：\n" + (raw.length > 200 ? raw.slice(0, 200) + "…" : raw);
  }
  if (/401|403|invalid.*api.*key/i.test(raw)) {
    return "API key 不对或失效。检查后端 .env 里的 ANTHROPIC_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY 并重启后端。";
  }

  // 4xx with JSON body
  const httpMatch = raw.match(/(\d{3})[^:]*:\s*(.+)/);
  if (httpMatch) {
    const status = httpMatch[1];
    const body = httpMatch[2];

    // Special-case: 404 on a *newly-added* endpoint usually means the
    // local backend hasn't been restarted since the frontend updated.
    // Generic "Not found" was confusing the user, so spell it out.
    if (status === "404") {
      const NEW_ENDPOINT_HINTS = [
        "external_reports", "integrated_reports", "upload_file",
        "/api/projects",     // v0.18+
      ];
      if (NEW_ENDPOINT_HINTS.some(h => raw.includes(h))) {
        return (
          "本地后端是旧版本，还没这个新接口。\n" +
          "→ 去后端那个终端 Ctrl-C，然后重跑：\n" +
          "    python -m studio serve --port 8765\n" +
          "（如果上次更新过 requirements，先 pip install -r requirements.txt）"
        );
      }
    }

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
