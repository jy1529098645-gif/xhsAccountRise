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
  };
}

export interface Brief {
  topic: string;
  angle: string;
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
}
