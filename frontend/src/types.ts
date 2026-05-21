export interface Library {
  lib_id: string;
  display_name: string;
  uploaded_at: number;
  source: string;
  notes_count: number;
  comments_count: number;
  size_bytes: number;
  platform: string;
  active: boolean;
}

export interface Platform {
  id: string;
  label: string;
}

export interface Status {
  active_library: { lib_id: string; display_name: string; notes: number };
  counts: Record<string, number>;
  providers: { anthropic: boolean; deepseek: boolean; openai: boolean };
}

export interface DnaSummary {
  total_notes_analysed: number;
  dominant_hooks: { category: string; count: number }[];
  generated_in_seconds: number;
}

export interface DnaArtifact {
  version: string;
  generated_at: number;
  summary: DnaSummary;
  sections: {
    titles: any;
    body_and_shape: any;
    timing: any;
    tags: any;
    keyword_blueocean: any;
    comment_demand: any;
    top_performers: any;
    /** v0.56: 笑点/梗/夸张戏谑 signal — added to DNA later. */
    humor_signals?: any;
    /** v0.57: 内容形式分布 (图文/短视频/长视频/...) — added to DNA later. */
    content_format?: any;
  };
}

export interface Brief {
  topic: string;
  angle: string;
  /** v0.52: multi-angle. Drafter pool produces one candidate per angle. */
  angles?: string[];
  target_length: number;
  cta_strength: "none" | "soft" | "strong";
  niche?: string;
  reference_note_ids?: string[];
  extra_constraints?: string;
  platform?: string;
}

export interface CandidatePayload {
  title: string;
  body: string;
  tags: string[];
  cover_prompt: string;
  hook_type: string;
  predicted_likes: number;
  self_score: number;
  self_critique: string;
  /** v0.52: which angle this draft was written for. */
  angle?: string;
}

export interface CritiqueScore {
  hook?: number;
  language_fit?: number;
  shareability?: number;
  brand_safety?: number;
  structural_clarity?: number;
}

export interface Critique {
  critic_llm: string;
  scores: CritiqueScore;
  risk_flags: string[];
  suggestion: string;
  overall: number;
}

export interface DraftCandidate {
  candidate_id: string;
  llm: string;
  error: string;
  latency_ms: number;
  cost_estimate_usd: number;
  token_usage: { input?: number; output?: number };
  payload: CandidatePayload;
  critiques: Critique[];
  critique_avg?: number | null;
}

export interface TraceStep {
  step_index: number;
  agent_name: string;
  llm: string;
  input_summary: string;
  output_summary: string;
  latency_ms: number;
  cost_estimate_usd: number;
  error: string;
}

export interface PlanSlot {
  slot: string;
  median_likes: number;
  why: string;
}
export interface PlanAngle {
  title: string;
  angle: string;
  hook_type: string;
  why: string;
}
export interface ExecutionPlan {
  publish_schedule?: PlanSlot[];
  follow_up_angles?: PlanAngle[];
  engagement_tactics?: string[];
  series_thesis?: string;
}

export interface ComposeBundle {
  draft_id: string;
  library_id: string;
  brief: Brief;
  strategy: {
    recommended_hook?: string;
    opening_hook?: string;
    structure?: string[];
    cta_phrase?: string;
    tone?: string;
    avoid?: string[];
  };
  plan?: ExecutionPlan;
  rag: { refs: { note_id: string; title: string; likes: number }[]; comments_count: number; hooks: string[] };
  drafts: DraftCandidate[];
  refined: DraftCandidate | null;
  final: DraftCandidate | null;
  trace: TraceStep[];
  totals: { cost_usd: number; elapsed_s: number };
  generated_at: number;
}

export interface DraftListItem {
  draft_id: string;
  generated_at: number;
  mode: string;
  library_id: string;
  final_candidate_id: string | null;
  final_title: string | null;
  candidate_count: number;
  brief: Brief;
}

export interface DraftDetail {
  draft: any;
  candidates: any[];
  trace: any[];
  plan?: ExecutionPlan;
  strategy?: any;
  /** v0.53: persisted RAG context for the provenance panel. */
  rag?: {
    refs: RagRef[];
    comments: RagComment[];
    hooks: RagHook[];
  };
  /** v0.53: child drafts spawned via variant fan-out. */
  variants?: VariantChild[];
}

export interface RagRef {
  note_id: string;
  title: string;
  liked_count: number;
  collected_count: number;
  comment_count: number;
  url?: string;
  body_excerpt: string;
  // v0.57: video-platform fields (Douyin / BiliBili). 0 when ref is a
  // photo carousel from xhs. ReferenceCard renders ▶︎{duration_sec}s when
  // duration_sec > 0, else falls back to xhs's 🖼️ image-count.
  duration_sec?: number;
  share_count?: number;
  image_count?: number;
  author_nickname?: string;
  tags?: string[];
  // v0.63: actual image URLs extracted from crawler raw_json so the
  // ProvenancePanel can show real thumbnails ("图文效果"). 0-4 URLs.
  // cover_image = image_urls[0] (convenience).
  image_urls?: string[];
  cover_image?: string;
}

export interface RagComment {
  comment_id: string;
  content: string;
  like_count: number;
  note_id?: string;
}

export interface RagHook {
  category: string;
  count: number;
  median_likes: number;
  examples: { title: string; liked_count: number }[];
}

export interface RagSearchResult {
  refs: RagRef[];
  comments: RagComment[];
  hooks: RagHook[];
  error?: string;
}

export interface VariantChild {
  draft_id: string;
  generated_at: number;
  variant_label: string | null;
  angle: string | null;
  final_title: string | null;
}

// ---- Compliance (item 1) -----------------------------------------------
export type ComplianceSeverity = "pass" | "warn" | "block";

export interface ComplianceHit {
  term: string;
  rule_id: string;
  category: string;
  severity: "block" | "warn";
  span_start: number;
  span_end: number;
  where: string;       // 'title' | 'body' | 'tags' | ...
  safe_alternative: string;
  rationale: string;
}

export interface ComplianceCheck {
  severity: ComplianceSeverity;
  hits: ComplianceHit[];
  hit_count: number;
  by_severity?: { block: number; warn: number };
}

// ---- Tracking (item 3) -------------------------------------------------
export interface TrackingFetchResult {
  status: "ok" | "no_url" | "no_note_id" | "no_crawler" | "http_err"
        | "rate_limited" | "no_ssr" | "parse_err";
  note_id: string | null;
  likes?: number | null;
  saves?: number | null;
  comments?: number | null;
  shares?: number | null;
  raw_summary: string;
  fetch_id?: string;
  perf_id?: string | null;
}

// ---- Product Context (v0.58) ------------------------------------------
export interface ProductContextDTO {
  context_id: string;
  project_id: string;
  name: string;
  source_format: "paste" | "pdf" | "docx" | "md" | "txt";
  source_filename?: string | null;
  chars: number;
  created_at: number;
  updated_at: number;
  active: 0 | 1;
  body_text?: string;    // only on getContext()
}

// ---- Feedback proposals (item 8) ---------------------------------------
export interface PromptProposal {
  proposal_id: string;
  review_id: string;
  generator_name: string;
  parent_version: string;
  proposed_version: string;
  diff_summary: string;
  expected_gain: string;
  evidence: { signal: string; why_changes_prompt: string }[];
  created_at: number;
  status: "pending" | "approved" | "rejected" | "superseded";
  decided_at?: number | null;
  decision_notes?: string | null;
  proposed_prompt?: string;       // populated only via getProposal()
}

// ---- Account launch strategy ---------------------------------------------

export interface AccountInputDTO {
  positioning: string;
  target_audience: string;
  cycle_weeks: number;
  posts_per_week: number;
  personal_strengths: string;
  constraints: string;
  platform: string;
  /** v0.52: angles the schedule should distribute slots across. Empty = AI free pick. */
  expected_angles?: string[];
  /** v0.55: ISO date 'YYYY-MM-DD' for the first day of the cycle. Empty = next Monday. */
  cycle_start_date?: string;
  /** v0.59: 起号目标分类 — personal_share / emotional / academic / product_saas / 等。Empty = 通用。 */
  goal_type?: string;
  /** v0.61.5: 启动阶段。"" / "auto" = AI 据 DNA + 报告自己推荐；
   *  "cold" = 0 粉 / 陌生人，主走人设痛点；"warm" = 已有粉丝/行业资源，可强转化；
   *  "hybrid" = 前期人设 + 后期转化的中间态。 */
  startup_phase?: string;
  /** v0.61.13: 内容形式偏好。"" / "auto" = AI 按 DNA 自决；
   *  "tuwen_only" = 全部图文；"video_only" = 全部短视频；"mixed" = 强制图文+视频混合。 */
  content_format_preference?: string;
}

/** v0.59: 8 大起号目标分类，前端 GoalPicker 渲染用。 */
export interface GoalTypeDTO {
  key: string;
  emoji: string;
  name: string;
  description: string;
  voice_hint: string;
  phase_emphasis: string;
  requires_product_context: boolean;
  recommended_product_context: boolean;
  example_directions: string[];
  prompt_addendum: string;
}

export interface StrategicDirectionDTO {
  name: string;
  positioning_statement: string;
  target_audience: string;
  hook_angles: string[];
  differentiator: string;
  risk: string;
  score: number;
  why_works: string;
}

export interface TopicSlotDTO {
  week: number;
  day_of_week: number;
  publish_slot: string;
  title: string;
  title_variants: string[];
  angle: string;
  hook_type: string;
  outline: string[];
  materials_needed: string[];
  intent: string;
  body_draft?: string;
  content_format?: string;  // 图文 / 短视频 / 长视频 / 直播 / 纯文本
  /** v0.55: ≤30 字 LLM explanation for why this time-slot suits this slot's content type. */
  publish_rationale?: string;
  /** v0.59: which user-picked direction this slot belongs to (0-indexed). -1 = legacy / untagged. */
  direction_idx?: number;
  /** v0.60: AI 一句话解释「为什么这周 + 为什么这角度」(≤40 字). 让用户看见 AI 的策略思路. */
  decision_rationale?: string;
  /** v0.61: AI 推荐的发布窗口（不是死锁单日单时段）. 例 "周三-周五任一晚 / 21:00-23:00". */
  flexible_window?: string;
  /** v0.62: 每个 slot 的 2 个次选方案。用户在 PackView 上选哪个 alt 后进 Composer。 */
  alternative_versions?: Array<{
    label?: string;
    publish_slot?: string;
    angle?: string;
    hook_type?: string;
    content_format?: string;
    title?: string;
    mini_outline?: string[];
    why_alt?: string;
  }>;
}

export interface WeekThemeDTO {
  week: number;
  theme: string;
  intent: string;
  notes: string;
}

export interface StrategyPackDTO {
  pack_id: string;
  library_id: string;
  platform: string;
  created_at: number;
  input: AccountInputDTO;
  chosen_direction: StrategicDirectionDTO;
  series_thesis: string;
  weekly_themes: WeekThemeDTO[];
  schedule: TopicSlotDTO[];
  materials_checklist: string[];
  risks_and_mitigations: string[];
  success_metrics: string[];
  /** v0.59: 多选 — 用户选了 N 个 directions 全部存这里。Empty 数组 = 单方向旧包。 */
  chosen_directions?: StrategicDirectionDTO[];
}

export interface StrategyListItem {
  pack_id: string;
  library_id: string | null;
  platform: string | null;
  created_at: number;
  updated_at: number;
  status: "directions" | "expanded" | "expanding" | "expand_failed" | "paused";
  input: AccountInputDTO;
  chosen_direction_idx: number | null;
  elapsed_s: number | null;
}

export interface StrategyDetail {
  pack_id: string;
  library_id: string | null;
  platform: string | null;
  created_at: number;
  updated_at: number;
  status: "directions" | "expanded" | "expanding" | "expand_failed" | "paused";
  input: AccountInputDTO;
  directions: StrategicDirectionDTO[];
  chosen_direction_idx: number | null;
  pack: StrategyPackDTO | null;
  elapsed_s: number | null;
}

// ---- Projects -----------------------------------------------------------

export interface ProjectDTO {
  project_id: string;
  name: string;
  description: string;
  emoji: string;
  is_default: boolean;
  archived: boolean;
  created_at: number;
  active: boolean;
}

// ---- Insight (library report) ------------------------------------------

export interface InsightReportDTO {
  report_id: string;
  library_id: string;
  project_id: string | null;
  created_at: number;
  status: "pending" | "completed" | "failed";
  elapsed_s: number | null;
  error: string | null;
  claude_analysis: any;
  openai_analysis: any;
  debate: any;
  consensus: any;
}
