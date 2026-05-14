import { AgentRoleId, AGENT_ROLES, COST_PRESETS, LLM_CATALOG } from "../catalog";

export interface AgentSelection {
  strategist: string[];
  drafter: string[];
  critic: string[];
  refiner: string[];
  synthesizer: string[];
  planner: string[];
  skip: Record<AgentRoleId, boolean>;
}

export function defaultSelection(): AgentSelection {
  const sel = COST_PRESETS["默认 (4o 起草 + Claude 融合最终稿 ★ 推荐)"];
  return {
    strategist: sel.strategist, drafter: sel.drafter, critic: sel.critic,
    refiner: sel.refiner, synthesizer: sel.synthesizer, planner: sel.planner,
    skip: { strategist: false, drafter: false, critic: false,
            refiner: false, synthesizer: false, planner: false },
  };
}

export function selectionToSpecs(sel: AgentSelection) {
  return {
    strategist_spec: sel.strategist.join(",") || "openai",
    drafter_spec: sel.drafter.join(",") || "openai",
    critic_spec: sel.critic.join(",") || "deepseek",
    refiner_spec: sel.refiner.join(",") || "openai",
    synthesizer_spec: sel.synthesizer.join(",") || "claude:sonnet",
    planner_spec: sel.planner.join(",") || "deepseek",
    skip_strategist: sel.skip.strategist,
    skip_critics: sel.skip.critic,
    skip_refiner: sel.skip.refiner,
    skip_synthesizer: sel.skip.synthesizer,
    skip_planner: sel.skip.planner,
  };
}

export default function AgentConfigPanel({
  selection, onChange,
}: {
  selection: AgentSelection;
  onChange: (s: AgentSelection) => void;
}) {
  function toggle(role: AgentRoleId, llmId: string) {
    const cur = selection[role];
    const next = AGENT_ROLES.find(r => r.id === role)!.multi
      ? (cur.includes(llmId) ? cur.filter(x => x !== llmId) : [...cur, llmId])
      : [llmId];
    onChange({ ...selection, [role]: next });
  }
  function toggleSkip(role: AgentRoleId) {
    onChange({ ...selection, skip: { ...selection.skip, [role]: !selection.skip[role] } });
  }
  function applyPreset(name: string) {
    const p = COST_PRESETS[name];
    if (!p) return;
    onChange({
      strategist: p.strategist, drafter: p.drafter, critic: p.critic,
      refiner: p.refiner, synthesizer: p.synthesizer, planner: p.planner,
      skip: selection.skip,
    });
  }
  function reset() { onChange(defaultSelection()); }

  return (
    <div className="agent-config">
      <div className="agent-config-toolbar">
        <div className="muted" style={{ fontSize: 12 }}>
          为每个角色选 AI。多选的角色（起草团 / 审稿团）会并行调用每个勾上的家。
        </div>
        <div className="row" style={{ gap: 6 }}>
          <select onChange={e => { if (e.target.value) { applyPreset(e.target.value); e.target.value = ""; } }}
            defaultValue=""
            style={{ fontSize: 12 }}>
            <option value="" disabled>切换预设…</option>
            {Object.keys(COST_PRESETS).map(name => <option key={name} value={name}>{name}</option>)}
          </select>
          <button className="ghost" onClick={reset} style={{ fontSize: 12 }}>↺ 恢复默认</button>
        </div>
      </div>

      {AGENT_ROLES.map(role => {
        const selected = selection[role.id];
        const skipped = selection.skip[role.id];
        return (
          <div key={role.id} className={`agent-role ${skipped ? "skipped" : ""}`}>
            <div className="agent-role-header">
              <div>
                <div className="agent-role-title">
                  {role.label}
                  <span className="agent-role-tag">{role.whatItProduces}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  {role.description}
                </div>
                <div className="muted" style={{ fontSize: 11, fontStyle: "italic", marginTop: 2 }}>
                  💡 {role.rationale}
                </div>
              </div>
              {role.canSkip && (
                <label style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }}>
                  <input type="checkbox" checked={skipped}
                    onChange={() => toggleSkip(role.id)}
                    style={{ marginRight: 4 }} />
                  跳过本步
                </label>
              )}
            </div>

            {!skipped && (
              <div className="agent-llm-grid">
                {LLM_CATALOG.map(llm => {
                  const on = selected.includes(llm.id);
                  return (
                    <button key={llm.id}
                      type="button"
                      className={`llm-chip ${on ? "on" : ""} cost-${llm.cost}`}
                      onClick={() => toggle(role.id, llm.id)}>
                      <div className="llm-chip-mark">{on ? (role.multi ? "☑" : "◉") : (role.multi ? "☐" : "○")}</div>
                      <div className="llm-chip-body">
                        <div className="llm-chip-label">{llm.label}</div>
                        <div className="llm-chip-hint">{llm.hint}</div>
                      </div>
                      <div className={`llm-chip-cost cost-${llm.cost}`}>
                        {llm.cost === "high" ? "贵" : llm.cost === "mid" ? "中" : "省"}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
