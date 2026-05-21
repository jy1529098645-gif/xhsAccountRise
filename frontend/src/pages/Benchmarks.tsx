import { useEffect, useState } from "react";
import { api } from "../api";
import { humaniseError } from "../errors";
import { fmtLikes, fmtRelative } from "../format";
import type {
  BenchmarkAccountDTO, BenchmarkAuthorSearchResult,
} from "../types";

// 对标账号 (v0.64) — 从当前 library 里挑账号，RAG 检索时给他们的笔记加权。
// 不抓外部数据 ：用户上传 library 时已经把这批账号的笔记带进来了；这里只是
// "在已有数据里告诉 AI 优先用这些人"。
export default function Benchmarks() {
  const [accounts, setAccounts] = useState<BenchmarkAccountDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<BenchmarkAuthorSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);

  // 备注/昵称编辑用 — 不开 modal，inline。
  const [pendingNote, setPendingNote] = useState<Record<string, string>>({});

  async function reload() {
    try {
      const r = await api.benchmarksList();
      setAccounts(r.accounts);
      setErr(null);
    } catch (e) {
      setErr(humaniseError(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { reload(); }, []);

  async function doSearch() {
    const q = query.trim();
    if (!q) { setSearchResults([]); setSearchErr(null); return; }
    setSearching(true);
    setSearchErr(null);
    try {
      const r = await api.benchmarksSearchAuthors(q, 30);
      setSearchResults(r.authors);
    } catch (e) {
      setSearchErr(humaniseError(e));
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }

  async function addAuthor(a: BenchmarkAuthorSearchResult) {
    try {
      await api.benchmarksAdd(a.author_id, a.author_nickname, "");
      // 乐观更新 ：把刚加的塞进 accounts，再异步对账
      setSearchResults(prev => prev.map(x =>
        x.author_id === a.author_id ? { ...x, already_added: true } : x
      ));
      await reload();
    } catch (e) {
      setErr(humaniseError(e));
    }
  }

  async function addManual() {
    const q = query.trim();
    if (!q) return;
    if (!confirm(`没在 library 里搜到这个账号，直接按"${q}"作为 author_id 加入吗？\n（如果你确定 author_id 是 ${q}，可以加。否则建议先搜 nickname 找到正确的 author_id。）`)) return;
    try {
      await api.benchmarksAdd(q, "", "");
      setQuery("");
      await reload();
    } catch (e) {
      setErr(humaniseError(e));
    }
  }

  async function removeAccount(account_id: string) {
    if (!confirm("移除这个对标账号？（不会删除 library 里的笔记，只是不再加权）")) return;
    try {
      await api.benchmarksRemove(account_id);
      setAccounts(prev => prev.filter(a => a.account_id !== account_id));
    } catch (e) {
      setErr(humaniseError(e));
    }
  }

  async function saveNote(account_id: string) {
    const note = pendingNote[account_id];
    if (note === undefined) return;
    const acct = accounts.find(a => a.account_id === account_id);
    try {
      await api.benchmarksAdd(account_id, acct?.nickname || "", note);
      setPendingNote(prev => {
        const { [account_id]: _, ...rest } = prev;
        return rest;
      });
      await reload();
    } catch (e) {
      setErr(humaniseError(e));
    }
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ margin: "0 0 8px" }}>🎯 对标账号</h2>
        <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>
          从当前 library 里挑账号加入"对标"。<b>出稿时 AI 会优先用他们的爆款</b>
          做参考（hybrid_score 上加一个 boost）。这里不抓新数据 — 想加的账号
          必须先出现在你上传的 library 里。
        </div>
      </div>

      {err && (
        <div className="card" style={{ borderColor: "var(--danger, #d33)", marginBottom: 16 }}>
          <b>出错了 ：</b> {err}
        </div>
      )}

      {/* 搜索 + 加入 ----------------------------------------- */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>🔎 在当前 library 里搜账号</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") doSearch(); }}
            placeholder="输入 nickname（模糊匹配）或完整 author_id"
            style={{ flex: 1, padding: "6px 10px" }}
          />
          <button className="btn btn-primary" onClick={doSearch} disabled={searching}>
            {searching ? "搜索中…" : "搜索"}
          </button>
          {query.trim() && searchResults.length === 0 && !searching && (
            <button className="btn" onClick={addManual} title="按 author_id 直接加（即使 library 里没匹配到）">
              直接按 ID 加入
            </button>
          )}
        </div>
        {searchErr && <div style={{ color: "var(--danger, #d33)", fontSize: 13 }}>{searchErr}</div>}
        {searchResults.length > 0 && (
          <table style={{ width: "100%", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                <th>账号</th>
                <th style={{ textAlign: "right" }}>笔记数</th>
                <th style={{ textAlign: "right" }}>最高赞</th>
                <th style={{ textAlign: "right" }}>累计赞</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {searchResults.map(a => (
                <tr key={a.author_id} style={{ borderTop: "1px solid var(--border, #eee)" }}>
                  <td style={{ padding: "6px 4px" }}>
                    <div>{a.author_nickname || <i style={{ color: "var(--muted)" }}>（无昵称）</i>}</div>
                    <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "monospace" }}>
                      {a.author_id}
                    </div>
                  </td>
                  <td style={{ textAlign: "right", padding: "6px 4px" }}>{a.note_count}</td>
                  <td style={{ textAlign: "right", padding: "6px 4px" }}>{fmtLikes(a.top_likes)}</td>
                  <td style={{ textAlign: "right", padding: "6px 4px" }}>{fmtLikes(a.total_likes)}</td>
                  <td style={{ textAlign: "right", padding: "6px 4px" }}>
                    {a.already_added ? (
                      <span style={{ color: "var(--muted)", fontSize: 12 }}>已加入</span>
                    ) : (
                      <button className="btn btn-primary" onClick={() => addAuthor(a)}>
                        + 加入对标
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {query.trim() && searchResults.length === 0 && !searching && !searchErr && (
          <div style={{ fontSize: 13, color: "var(--muted)" }}>
            没在当前 library 里搜到。要么换关键词，要么先去 <a href="#/settings">设置 → 库管理</a>
            上传包含这个账号的数据库，要么用上面"直接按 ID 加入"。
          </div>
        )}
      </div>

      {/* 当前对标列表 ----------------------------------------- */}
      <div className="card">
        <h3 style={{ marginTop: 0 }}>
          📌 当前对标账号 <span style={{ fontSize: 13, color: "var(--muted)", fontWeight: "normal" }}>
            （{accounts.length} 个）
          </span>
        </h3>
        {loading ? (
          <div style={{ color: "var(--muted)" }}>加载中…</div>
        ) : accounts.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.6 }}>
            还没加任何对标账号。在上面搜索框里找一个开始吧 — RAG 检索会立刻生效，
            下次出稿时 AI 就会优先用他们的爆款。
          </div>
        ) : (
          <table style={{ width: "100%", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                <th>账号</th>
                <th style={{ textAlign: "right" }}>笔记数</th>
                <th>最高赞篇</th>
                <th>我的备注</th>
                <th>加入时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map(a => (
                <tr key={a.account_id} style={{ borderTop: "1px solid var(--border, #eee)" }}>
                  <td style={{ padding: "8px 4px" }}>
                    <div>
                      {a.nickname || <i style={{ color: "var(--muted)" }}>（无昵称）</i>}
                      {a.missing_in_library && (
                        <span style={{ marginLeft: 6, fontSize: 11, color: "var(--danger, #d33)" }}>
                          · 当前 library 里没匹配的笔记
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "monospace" }}>
                      {a.account_id}
                    </div>
                  </td>
                  <td style={{ textAlign: "right", padding: "8px 4px" }}>{a.note_count}</td>
                  <td style={{ padding: "8px 4px", maxWidth: 280 }}>
                    {a.top_title ? (
                      a.top_url ? (
                        <a href={a.top_url} target="_blank" rel="noreferrer"
                          style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={a.top_title}>
                          {a.top_title}
                        </a>
                      ) : (
                        <span title={a.top_title} style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {a.top_title}
                        </span>
                      )
                    ) : <span style={{ color: "var(--muted)" }}>—</span>}
                    {a.top_likes > 0 && (
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>
                        {fmtLikes(a.top_likes)} 赞
                      </div>
                    )}
                  </td>
                  <td style={{ padding: "8px 4px", minWidth: 160 }}>
                    <input
                      value={pendingNote[a.account_id] ?? a.note}
                      onChange={e => setPendingNote(prev => ({ ...prev, [a.account_id]: e.target.value }))}
                      onBlur={() => {
                        if (pendingNote[a.account_id] !== undefined &&
                            pendingNote[a.account_id] !== a.note) {
                          saveNote(a.account_id);
                        }
                      }}
                      placeholder="为什么标这个？（可选）"
                      style={{ width: "100%", fontSize: 12, padding: "3px 6px" }}
                    />
                  </td>
                  <td style={{ padding: "8px 4px", fontSize: 12, color: "var(--muted)" }}>
                    {fmtRelative(a.added_at)}
                  </td>
                  <td style={{ textAlign: "right", padding: "8px 4px" }}>
                    <button className="btn" onClick={() => removeAccount(a.account_id)}>
                      移除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
