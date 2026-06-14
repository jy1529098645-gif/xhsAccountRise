# 可复用模式库 · 从 xhsAccountRise v0.63.3 清理沉淀

> 把这整份 markdown 贴进任何 Claude / Cursor 对话，再加一句
> 「把这些模式套到我当前项目里」，AI 就能直接照搬。
>
> 适用 ：React + TypeScript + Vite 前端 / FastAPI + SQLite 后端。
> 其它栈（Vue / Express / Postgres）换语法但思路一致。

---

## 🎯 给 AI 的指令（贴这段到对话最前）

> 我给你 6 个生产环境验证过的模式。请检查我当前项目，找出适用场景，
> **逐项**问我要不要套用 ：
>
> 1. `throwHttpError` ：DELETE/PATCH 端点是否有重复的 4 行 `await res.text() + throw new HttpError(...)` 块？
> 2. `humaniseError` ：alert / banner 是否在裸吐 `{"detail":"..."}` JSON？
> 3. `useAsyncFetch` hook ：组件里是否有 ≥2 处重复的 `useState(data) + useState(loading) + useState(err) + useEffect(fetch)` 模板？
> 4. 「永不 500」端点 ：被前端每次 render 都调用的 GET 端点，失败时是否 500 让前端只能显示「加载失败」？
> 5. SQLite 缺表防御 ：feature-detect 列 + try FTS / catch fallback to LIKE，是否有 schema 漂移导致的 500？
> 6. `logError` ：是否有 `.catch(() => {})` 这种把异常完全吞掉、DevTools 都看不到的代码？
>
> **不要一上来就重构所有**，先列出命中的位置 + 估算工作量 + 让我挑要做哪几项。

---

## 1. `throwHttpError` — REST 错误格式化共用

**问题 ：** 每个 DELETE/PATCH/POST endpoint 调用都写一遍
```ts
const text = await res.text().catch(() => "");
throw new HttpError(res.status, `DELETE /api/x → ${res.status}: ${text.slice(0, 400)}`);
```
3-4 处重复 = 改格式得改 3-4 处。

**放哪 ：** `src/api.ts` 顶部，紧挨着 `HttpError` class

```ts
class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message); this.status = status;
  }
}

// 共用错误格式化 — HttpError.message 永远是
// `${METHOD} ${path} → ${status}: ${body}`。这个 shape 是 humaniseError()
// 解析 FastAPI {"detail":"..."} 的契约，别乱改格式。
async function throwHttpError(method: string, path: string, res: Response): Promise<never> {
  const text = await res.text().catch(() => "");
  throw new HttpError(res.status, `${method} ${path} → ${res.status}: ${text.slice(0, 400)}`);
}
```

**用法 ：**
```ts
deleteThing: async (id: string) => {
  const backend = backendUrl();
  if (!backend) throw new HttpError(0, "需要本地后端");
  const path = `/api/things/${encodeURIComponent(id)}`;
  const res = await fetch(`${backend}${path}`, { method: "DELETE" });
  if (!res.ok) await throwHttpError("DELETE", path, res);
  return res.json();
}
```

---

## 2. `humaniseError` — HTTP 错误翻译成人话

**问题 ：** `alert("失败 ：" + e.message)` 把 `{"detail":"cannot delete active library; switch first"}` 这种 JSON 直接糊用户脸上，看上去像「报错不让我用」。

**放哪 ：** `src/errors.ts`

```ts
export function humaniseError(e: unknown): string {
  const raw = (e instanceof Error ? e.message : String(e ?? "")) || "未知错误";

  // 网络 / CORS / 后端没起来
  if (/Failed to fetch|NetworkError|TypeError.*fetch|net::ERR/.test(raw)) {
    return (
      "调用中断了。可能是 ：\n" +
      "  · 长时间请求被网络断开 — 稍等再点 ↻ 重试\n" +
      "  · 本地后端没起来 — 看顶部黄条命令启动\n" +
      "  · 浏览器拦截 HTTPS→localhost"
    );
  }
  if (/timeout|abort/i.test(raw)) return "调用超时，稍等再试。";

  // LLM provider 业务错误（按你的栈改）
  if (/Insufficient Balance|insufficient_balance|余额不足/i.test(raw))
    return "💰 余额不足 — 去 provider 网站充值。";
  if (/insufficient_quota|exceeded.*quota/i.test(raw))
    return "💰 OpenAI quota 用完 — 检查 platform.openai.com/usage。";
  if (/rate.?limit|rate_limit_exceeded|Too Many Requests|429/i.test(raw))
    return "⏳ API 限速（RPM/TPM 超）。等 30-60s 再点。";
  if (/401|403|invalid.*api.*key/i.test(raw))
    return "API key 不对或失效。检查后端 .env 并重启。";

  // 4xx + FastAPI 的 {"detail":"..."} — 配合 throwHttpError 的格式
  const httpMatch = raw.match(/(\d{3})[^:]*:\s*(.+)/);
  if (httpMatch) {
    const status = httpMatch[1];
    const body = httpMatch[2];
    try {
      const j = JSON.parse(body);
      if (j?.detail) {
        if (status === "422") return `📄 文件被拒：${j.detail}`;
        if (status === "404") return `没找到对象：${j.detail}`;
        return j.detail;
      }
    } catch { /* fall through */ }
  }
  return raw.length > 400 ? raw.slice(0, 400) + "…" : raw;
}
```

**用法 ：**
```ts
try { await api.deleteThing(id); }
catch (e) {
  console.error("[X] delete failed", e);
  alert("删除失败 ：\n" + humaniseError(e));
}
```

**后端配套** ：错误信息直接写中文人话 + emoji + 可操作的下一步，让前端透传 ：
```python
raise RuntimeError(
    "🛑 这是当前激活的资源库，先切到其它库再删 — 否则 RAG 会指向空。"
)
```

---

## 3. `useAsyncFetch` — 通用异步取数 hook

**问题 ：** 组件里到处都是
```tsx
const [data, setData] = useState(null);
const [loading, setLoading] = useState(true);
const [err, setErr] = useState(null);
const [reloadKey, setReloadKey] = useState(0);
useEffect(() => {
  let cancel = false;
  setLoading(true); setErr(null);
  api.xxx(...).then(d => !cancel && setData(d))
              .catch(e => !cancel && setErr(...))
              .finally(() => !cancel && setLoading(false));
  return () => { cancel = true; };
}, [..., reloadKey]);
```
3+ 处就该抽出来。

**放哪 ：** `src/hooks/useAsyncFetch.ts`

```ts
import { useEffect, useState } from "react";
import { humaniseError } from "../errors";

export interface AsyncFetchState<T> {
  data: T | null;
  loading: boolean;
  err: string | null;
  retry: () => void;
}

// 通用异步取数 hook ：自带 retry / cancel-on-unmount / 错误翻译。
// enabled=false 跳过请求（输入还没准备好时）。
// extraDeps 变了重新拉，不动 fetcher 引用。
export function useAsyncFetch<T>(
  fetcher: () => Promise<T>,
  enabled: boolean,
  extraDeps: readonly unknown[] = [],
  label = "useAsyncFetch",
): AsyncFetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    let cancel = false;
    setLoading(true); setErr(null);
    fetcher()
      .then(d => { if (!cancel) setData(d); })
      .catch((e: unknown) => {
        if (cancel) return;
        // eslint-disable-next-line no-console
        console.error(`[${label}] fetch failed`, e);
        const human = humaniseError(e);
        const raw = e instanceof Error ? e.message : String(e ?? "");
        setErr(human === raw ? human : `${human}\n（原始 ：${raw.slice(0, 200)}）`);
      })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reloadKey, ...extraDeps]);

  return { data, loading, err, retry: () => setReloadKey(k => k + 1) };
}
```

**用法 ：**
```tsx
const { data, loading, err, retry } = useAsyncFetch(
  () => api.searchPosts(query),
  query.length >= 3,            // enabled
  [userId],                     // extraDeps — userId 变了重新拉
  "PostsList",                  // label — console.error 里好认
);

if (loading) return <Skeleton />;
if (err) return <ErrorCard message={err} onRetry={retry} />;
return <PostsList posts={data?.posts ?? []} />;
```

---

## 4. 「永不 500」端点模式

**问题 ：** 前端 panel 每次 render 都调的 GET，后端 500 让用户只能看到「加载失败」，反而比 200+空数据糟糕。

**放哪 ：** 任何被频繁调用 + 失败时前端只显示开关性 UI 的 GET endpoint

**坏的 ：**
```python
@app.get("/api/search")
def search(q: str) -> dict:
    return do_search(q)   # 抛任何错都变 500
```

**好的 ：**
```python
@app.get("/api/search")
def search(q: str, k: int = 8) -> dict[str, Any]:
    """永远 200 + {results, error?}。前端 panel 每次 render 都调，500 会
    退化成「加载失败」黑盒，返回结构化空 + error hint 让前端能区分。"""
    q = (q or "").strip()
    if not q:
        return {"results": [], "error": "query 为空"}
    if len(q) > 200:
        q = q[:200]   # 防止病态长 query
    try:
        out = do_search(q, k=k)
    except Exception as exc:  # noqa: BLE001
        return {"results": [], "error": f"检索失败 ：{type(exc).__name__}: {exc}"[:300]}
    out.setdefault("results", [])
    return out
```

**前端配套 ：**
```ts
const d = await api.search(q);
if (d.error && d.results.length === 0) {
  // 后端给出了具体错误原因，比「加载失败」信息密度高
  setBanner(d.error);
}
setResults(d.results);
```

**判断要不要套这个模式 ：**
- ✅ 前端每次 render / 路由切换都会调的端点
- ✅ 失败时 UI 退化成空状态更好（vs 弹错误对话框）
- ❌ POST 提交（用户主动操作）— 那种 4xx/5xx 让 humaniseError 处理更合适

---

## 5. SQLite 缺表 / schema 漂移防御

**问题 ：** 不同 DB 实例可能有不同 schema（旧库没新列、xlsx 导入的库没 FTS）。一个 `sqlite3.OperationalError` 让整个端点 500。

**放哪 ：** 任何「同一份代码读多个独立 SQLite 文件」的场景

```python
import logging
import sqlite3
from typing import Any

_log = logging.getLogger(__name__)


def search_notes(topic: str) -> list[dict[str, Any]]:
    """FTS5 缺表（xlsx 导入未跑过 auto_build_fts）时退化到 LIKE。
    只有连基础表都没了才返回 []。"""
    with db.connect(read_only=True) as con:
        # 1) feature-detect 可选列
        try:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(notes)")}
        except sqlite3.OperationalError:
            return []
        if not cols:
            return []
        extra_cols = [c for c in ["video_duration_ms", "share_count"] if c in cols]
        extra_sql = (", " + ", ".join(extra_cols)) if extra_cols else ""

        # 2) 直接 try FTS，catch 落地到 LIKE — 比预先 SELECT 1 探针省一次 round-trip
        rows: list[dict[str, Any]] = []
        try:
            cur = con.execute(
                f"SELECT note_id, title{extra_sql}, bm25(fts_notes) AS bm"
                f" FROM fts_notes JOIN notes USING(note_id)"
                f" WHERE fts_notes MATCH ? ORDER BY bm LIMIT 50",
                (topic,),
            )
            rows = [dict(r) for r in cur]
        except sqlite3.OperationalError as exc:
            _log.warning("FTS unavailable: %s — fallback to LIKE", exc)

        # 3) LIKE 兜底（FTS 缺表 OR 命中 0 都走这里）
        if not rows:
            try:
                cur = con.execute(
                    f"SELECT note_id, title{extra_sql}, 0 AS bm"
                    f" FROM notes WHERE title LIKE ? LIMIT 50",
                    (f"%{topic}%",),
                )
                rows = [dict(r) for r in cur]
            except sqlite3.OperationalError as exc:
                _log.warning("LIKE fallback failed: %s", exc)
                return []
    return rows


def retrieve_for_brief(topic: str) -> dict[str, Any]:
    """一个 branch 挂掉不能拖累其它 branch — 返回拿到的部分数据。"""
    out: dict[str, list] = {"notes": [], "comments": [], "hooks": []}
    for key, func in [
        ("notes", lambda: search_notes(topic)),
        ("comments", lambda: search_comments(topic)),
        ("hooks", lambda: fetch_hook_summaries()),
    ]:
        try:
            out[key] = func()
        except Exception as exc:  # noqa: BLE001
            _log.exception("retrieve.%s failed: %s", key, exc)
    return out
```

---

## 6. `logError` — 别让 silent catch 完全吞错

**问题 ：** `.then(set).catch(() => {})` 把异常完全吃掉，DevTools 里看不到、support 工单查不出原因。

**放哪 ：** `src/lib/log.ts`

```ts
// 用法 ：`.catch(logError("Component.op"))`
// 保留「UI 不弹 banner」的安静行为，但 DevTools 看得到。
export const logError = (where: string) => (e: unknown) => {
  // eslint-disable-next-line no-console
  console.error(`[${where}]`, e);
};
```

**用法 ：**
```ts
// 改前
api.platforms().then(setPlatforms).catch(() => {});
api.libraries().then(setLibs).catch(() => {});

// 改后
api.platforms().then(setPlatforms).catch(logError("Composer.platforms"));
api.libraries().then(setLibs).catch(logError("Composer.libraries"));
```

写 `.catch(e => console.error("[X] op", e))` 也行 — 但项目里有 ≥10 处就值得抽出来。

---

## ⚙️ 套用顺序建议

如果一个项目都没做过这些，按这个顺序最稳 ：

1. **先 `humaniseError`** — 用户看到的错误立刻变中文人话，0 风险纯加新文件
2. **再 `throwHttpError`** — 让 humaniseError 能拿到结构化错误
3. **再 silent catch 加 `logError`** — 0 行为变化，纯加可观测性
4. **「永不 500」模式** — 选 1-2 个被 panel 频繁调的端点改造
5. **SQLite 防御** — 只在多 schema 场景下才需要
6. **`useAsyncFetch`** — 等组件里至少有 3 处重复模板再抽，过早抽象不值

每步独立可回滚。
