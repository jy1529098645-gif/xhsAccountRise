import { Link } from "react-router-dom";
import { useJobsList, cancelJob, clearJob } from "../lib/jobs";

const KIND_TO_LINK: Record<string, { label: string; to: string; emoji: string }> = {
  compose:        { emoji: "✍️", label: "出稿",   to: "/composer" },
  insight:        { emoji: "📊", label: "分析报告", to: "/reports" },
  // v0.62.5 ：wizard 全部并入 Composer — 这三类 job 都从 /composer 触发
  expand:         { emoji: "🚀", label: "起号策略", to: "/composer" },
  propose:        { emoji: "🚀", label: "起号策略", to: "/composer" },
  autofill:       { emoji: "🪄", label: "拟初稿",   to: "/composer" },
  retrospective: { emoji: "📊", label: "复盘",     to: "/retrospective" },
  integrate:     { emoji: "🪄", label: "整合报告", to: "/reports" },
};

const SECONDS = (ms: number) => Math.floor(ms / 1000);

export default function RunningJobsIndicator() {
  const jobs = useJobsList();
  const interesting = jobs.filter(j =>
    j.status === "running"
    || (j.status === "done" && Date.now() - (j.finishedAt ?? 0) < 30_000)
    || (j.status === "failed" && Date.now() - (j.finishedAt ?? 0) < 30_000)
  );
  if (interesting.length === 0) return null;

  return (
    <div style={{
      padding: "8px 10px", margin: "8px 0",
      background: "rgba(99, 102, 241, 0.08)",
      borderRadius: 6, fontSize: 12,
      border: "1px solid rgba(99, 102, 241, 0.2)",
    }}>
      <div style={{fontWeight: 600, marginBottom: 6, fontSize: 11.5, color: "#4338ca"}}>
        🔄 后台任务 ({interesting.length})
      </div>
      {interesting.map(j => {
        const k = KIND_TO_LINK[j.kind] ?? { emoji: "⚙️", label: j.kind, to: "/" };
        const elapsed = SECONDS(Date.now() - j.startedAt);
        const tone = j.status === "running"
          ? "#4338ca"
          : j.status === "done" ? "var(--ok)" : "var(--danger)";
        return (
          <div key={j.id} style={{display: "flex", alignItems: "center", gap: 6,
                                    fontSize: 11, padding: "3px 0", color: tone}}>
            <Link to={k.to} style={{flex: 1, minWidth: 0, color: "inherit",
                                     overflow: "hidden", textOverflow: "ellipsis",
                                     whiteSpace: "nowrap"}}>
              {k.emoji} {k.label}
              {j.meta?.topic && <> ：{String(j.meta.topic).slice(0, 16)}</>}
            </Link>
            {j.status === "running" ? (
              <>
                <span style={{opacity: 0.6}}>{elapsed}s</span>
                <button onClick={() => cancelJob(j.id)} className="ghost"
                  style={{padding: "1px 5px", fontSize: 10, color: "inherit"}}>⏸</button>
              </>
            ) : j.status === "done" ? (
              <>
                <span style={{fontSize: 11}}>✓</span>
                <button onClick={() => clearJob(j.id)} className="ghost"
                  style={{padding: "1px 5px", fontSize: 10, color: "inherit"}}>×</button>
              </>
            ) : (
              <>
                <span style={{fontSize: 11}}>✗</span>
                <button onClick={() => clearJob(j.id)} className="ghost"
                  style={{padding: "1px 5px", fontSize: 10, color: "inherit"}}>×</button>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
