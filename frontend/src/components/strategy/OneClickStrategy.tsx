// v0.66 (一键起号) ：把「拟账号信息 → 出方向 → 选最优方向 → 排期 expand」
// 这条原本要点 4-5 步的链路，压成**一个按钮、零必填**。
//
// AI 自动 ：autofill 据库 + DNA 拟账号定位/受众/周期 → propose 出带 score 的
// 多个方向 → 自动选最高分方向 → expand 出完整时间线 + 指标 + 材料。
// 用户只需点一下（可选填一句话描述账号/产品提升相关度），全程不用做选择。
// 想精细控制的人仍可走下面的「逐步定制」向导。

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, isAborted } from "../../api";
import { defaultCycleStartDate } from "../../format";
import { humaniseErrorAsync } from "../../errors";
import type { StrategicDirectionDTO } from "../../types";

type Stage = "idle" | "autofill" | "propose" | "expand" | "done" | "error";

const STAGE_LABEL: Record<Stage, string> = {
  idle: "",
  autofill: "🤖 1/3 AI 看你的库，自动拟账号定位 / 受众 / 节奏…",
  propose: "🤖 2/3 生成多个差异化方向，自动挑最优…",
  expand: "🤖 3/3 排完整时间线 + 成功指标 + 材料清单…",
  done: "✅ 完成！",
  error: "",
};

export default function OneClickStrategy({ onManual }: { onManual?: () => void }) {
  const navigate = useNavigate();
  const [hint, setHint] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [err, setErr] = useState<string | null>(null);
  const [pickedName, setPickedName] = useState<string>("");
  const running = stage === "autofill" || stage === "propose" || stage === "expand";

  async function run() {
    setErr(null); setPickedName("");
    try {
      // 1) autofill —— 据库 + DNA 自动拟账号信息（hint 作为可选个性化提示）
      setStage("autofill");
      const af = await api.autofillStrategy({
        personal_hint: hint.trim(),
        constraints_hint: "",
        deep: false,
      });
      const input = {
        ...af.input,
        positioning: af.input.positioning || hint.trim() || "",
        target_audience: af.input.target_audience || "",
        cycle_weeks: af.input.cycle_weeks || 4,
        posts_per_week: af.input.posts_per_week || 3,
        cycle_start_date: af.input.cycle_start_date || defaultCycleStartDate(),
        goal_type: "",
      };

      // 2) propose —— 出方向
      setStage("propose");
      const prop = await api.proposeStrategy({
        ...input,
        positioning: input.positioning,
        target_audience: input.target_audience,
      });
      const dirs: StrategicDirectionDTO[] = prop.directions || [];
      if (!prop.pack_id || dirs.length === 0) {
        throw new Error("AI 没出有效方向 — 库里相关内容可能太少，换一句话描述再试。");
      }
      // 自动选 score 最高的方向
      let bestIdx = 0;
      for (let i = 1; i < dirs.length; i++) {
        if ((dirs[i].score ?? 0) > (dirs[bestIdx].score ?? 0)) bestIdx = i;
      }
      setPickedName(dirs[bestIdx].name || "");

      // 3) expand —— 排完整时间线
      setStage("expand");
      await api.expandStrategy(prop.pack_id, bestIdx);

      setStage("done");
      navigate(`/strategy/${prop.pack_id}`);
    } catch (e: any) {
      if (isAborted(e)) { setStage("idle"); return; }
      setErr(await humaniseErrorAsync(e));
      setStage("error");
    }
  }

  return (
    <div className="card" style={{ borderLeft: "4px solid var(--primary)", background: "var(--primary-soft)" }}>
      <h2 style={{ marginTop: 0 }}>🚀 一键起号（全自动）</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: -4, lineHeight: 1.7 }}>
        点一下，AI 自动据你的资源库出一份**完整起号方案**：账号定位 → 方向 → 一整轮时间线 +
        成功指标 + 对标素材。全程不用做选择，出来后想改哪条改哪条。
      </p>

      <div style={{ marginBottom: 10 }}>
        <input
          value={hint}
          onChange={e => setHint(e.target.value)}
          disabled={running}
          placeholder="（可留空）一句话描述你的账号/产品，让方案更贴 — 例：帮留学生用AI写论文不被查AI率"
          style={{ width: "100%" }}
        />
      </div>

      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <button onClick={run} disabled={running}
          style={{ fontSize: 15, padding: "10px 22px", fontWeight: 600 }}>
          {running ? "🤖 生成中…（约 2-3 分钟，可切走不影响）" : "🚀 一键生成完整方案"}
        </button>
        {onManual && !running && (
          <button className="ghost" onClick={onManual} style={{ fontSize: 13 }}>
            或逐步定制（向导）↓
          </button>
        )}
      </div>

      {running && (
        <div style={{ marginTop: 12, padding: "10px 12px", background: "#fff", borderRadius: 8, fontSize: 13 }}>
          <div style={{ fontWeight: 600, color: "var(--primary)" }}>{STAGE_LABEL[stage]}</div>
          {pickedName && stage === "expand" && (
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              已自动选定方向：<b>{pickedName}</b>
            </div>
          )}
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            三步全自动跑完会直接打开方案页。中途可以去别的板块，回来不影响。
          </div>
        </div>
      )}

      {err && (
        <div className="banner danger" style={{ marginTop: 10, display: "flex",
          justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div style={{ whiteSpace: "pre-wrap", flex: 1 }}>{err}</div>
          <button className="secondary" style={{ padding: "4px 10px", fontSize: 12 }}
            onClick={run}>↻ 重试</button>
        </div>
      )}
    </div>
  );
}
