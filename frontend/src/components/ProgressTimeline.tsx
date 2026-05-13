import { useEffect, useState } from "react";

export interface Stage {
  /** Label shown to the user. AI first-person works great. */
  label: string;
  /** Rough expected duration in seconds. Animation paces itself by this. */
  durationSec: number;
  /** Optional sub-label that appears while the stage is active. */
  sub?: string;
}

interface Props {
  stages: Stage[];
  /** -1 = not started; 0..N-1 = current; >=N = done. Auto-advances if `auto`. */
  currentIndex: number;
  /** When true, the component animates currentIndex up to the last stage on its
   * own (using each stage's durationSec). Parent should NOT also drive
   * currentIndex when auto is on. Useful when the underlying API call is a
   * single black-box request with no progress callbacks. */
  auto?: boolean;
  /** Pinned to "done" with all stages green when true (after the API call
   * returns). */
  done?: boolean;
  /** Error text — turns the bar red and stops animation. */
  error?: string | null;
}

export default function ProgressTimeline({
  stages, currentIndex, auto, done, error,
}: Props) {
  const [autoIdx, setAutoIdx] = useState(0);

  useEffect(() => {
    if (!auto || done || error) return;
    if (autoIdx >= stages.length - 1) return;  // hold on last stage
    const ms = (stages[autoIdx]?.durationSec ?? 10) * 1000;
    const t = setTimeout(() => setAutoIdx(i => i + 1), ms);
    return () => clearTimeout(t);
  }, [auto, autoIdx, stages, done, error]);

  // Reset auto-index when caller turns auto on fresh
  useEffect(() => {
    if (auto && !done && !error) setAutoIdx(0);
  }, [auto]);

  const idx = done ? stages.length : (auto ? autoIdx : currentIndex);
  const totalSec = stages.reduce((s, x) => s + x.durationSec, 0);
  const elapsedSec = stages.slice(0, Math.min(idx, stages.length))
    .reduce((s, x) => s + x.durationSec, 0);
  const pct = error ? 100 : Math.min(100, (elapsedSec / Math.max(1, totalSec)) * 100);

  return (
    <div className="progress-timeline">
      <div className="progress-bar-outer">
        <div
          className={`progress-bar-fill ${error ? "error" : done ? "done" : "running"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <ol className="progress-stages">
        {stages.map((s, i) => {
          const state =
            error && i === idx ? "error" :
            done || i < idx ? "done" :
            i === idx ? "active" : "pending";
          const icon =
            state === "error" ? "✗" :
            state === "done" ? "✓" :
            state === "active" ? "⏳" : "○";
          return (
            <li key={i} className={`progress-stage ${state}`}>
              <span className="progress-stage-icon">{icon}</span>
              <span className="progress-stage-label">{s.label}</span>
              {state === "active" && s.sub && (
                <span className="progress-stage-sub">{s.sub}</span>
              )}
              <span className="progress-stage-time">~{s.durationSec}s</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
