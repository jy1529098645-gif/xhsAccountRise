import type {
  Brief, ComposeBundle, DnaArtifact, DraftDetail, DraftListItem,
  Library, Platform, Status,
  AccountInputDTO, StrategicDirectionDTO, StrategyDetail, StrategyListItem, StrategyPackDTO,
  ProjectDTO, InsightReportDTO,
  ComplianceCheck, ComplianceHit, PromptProposal,
  ProductContextDTO, GoalTypeDTO,
} from "./types";

const STATIC_PLATFORMS: Platform[] = [
  { id: "xiaohongshu", label: "小红书" },
  { id: "douyin", label: "抖音" },
  { id: "kuaishou", label: "快手" },
  { id: "bilibili", label: "B站" },
  { id: "youtube", label: "YouTube" },
  { id: "reddit", label: "Reddit" },
  { id: "x", label: "X / Twitter" },
  { id: "other", label: "其他" },
];

const KEY = "studio.backendUrl";
const DISABLED_KEY = "studio.backendDisabled";
const STATIC_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/data`;
const DEFAULT_BACKEND = "http://127.0.0.1:8765";

// v0.54: when the SPA is served from the same origin as a working backend
// (i.e. the one-container Docker deploy), prefer that origin instead of
// the local-dev default. Detected by checking whether the current page is
// served from something OTHER than a Vite dev server / file:// — i.e. has
// any non-loopback origin that responds at /api/health. We do this
// synchronously by inspecting the page origin; an explicit user override in
// localStorage always wins so dev workflow isn't broken.
function _sameOriginCandidate(): string | null {
  if (typeof window === "undefined") return null;
  const loc = window.location;
  if (loc.protocol === "file:" || !loc.origin) return null;
  // Vite dev server runs on 5173 — definitely NOT our backend. Skip auto.
  if (loc.port === "5173") return null;
  // Static GitHub Pages deploy → don't auto-use same origin (no backend there).
  if (/github\.io$/.test(loc.hostname)) return null;
  return loc.origin;
}

export function backendUrl(): string {
  if (localStorage.getItem(DISABLED_KEY) === "1") return "";
  // 1) Explicit user setting always wins.
  const explicit = localStorage.getItem(KEY);
  if (explicit) return explicit.replace(/\/$/, "");
  // 2) Cloud deploy: SPA + backend on same origin.
  const sameOrigin = _sameOriginCandidate();
  if (sameOrigin) return sameOrigin;
  // 3) Local dev fallback.
  return DEFAULT_BACKEND;
}
export function setBackendUrl(url: string) {
  if (url) {
    localStorage.setItem(KEY, url.replace(/\/$/, ""));
    localStorage.removeItem(DISABLED_KEY);
  } else {
    // explicit "clear" means: pretend offline (use static demo only)
    localStorage.removeItem(KEY);
    localStorage.setItem(DISABLED_KEY, "1");
  }
}
export function resetBackendUrl() {
  localStorage.removeItem(KEY);
  localStorage.removeItem(DISABLED_KEY);
}
export const DEFAULT_BACKEND_URL = DEFAULT_BACKEND;

class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message); this.status = status;
  }
}

async function getJson<T>(apiPath: string, staticPath?: string): Promise<T> {
  const backend = backendUrl();
  if (backend) {
    const res = await fetch(`${backend}${apiPath}`);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new HttpError(res.status, `${apiPath} → ${res.status}: ${text.slice(0, 200)}`);
    }
    return res.json();
  }
  if (!staticPath) throw new HttpError(0, `Offline · 此功能需要本地后端 (Settings 中配置)`);
  const res = await fetch(`${STATIC_BASE}/${staticPath}`);
  if (!res.ok) throw new HttpError(res.status, `${staticPath} 不存在 — 跑过 \`studio export-public\` 吗？`);
  return res.json();
}

async function postJson<T>(path: string, body: any, signal?: AbortSignal): Promise<T> {
  const backend = backendUrl();
  if (!backend) throw new HttpError(0, `此操作需要本地后端，请去 Settings 配置 backend URL`);
  const res = await fetch(`${backend}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new HttpError(res.status, `${path} → ${res.status}: ${text.slice(0, 400)}`);
  }
  return res.json();
}

// A helper for the user-cancelable flows. Wraps a promise that completes via
// fetch, surfacing AbortError as a distinct condition so the page can show
// '已暂停' instead of an error banner.
export class AbortedError extends Error {
  constructor() { super("aborted"); this.name = "AbortedError"; }
}
export function isAborted(e: unknown): boolean {
  if (e instanceof AbortedError) return true;
  if (e instanceof DOMException && e.name === "AbortError") return true;
  const msg = e instanceof Error ? e.message : String(e ?? "");
  return /aborted|AbortError/i.test(msg);
}

/** Backend-side cancel: tells the running pipeline to stop at the next
 * stage boundary (which actually kills the in-flight LLM call too). The
 * pipeline saves partial state so the next start picks up from where it
 * left off. Doesn't throw — best-effort. */
export async function cancelBackendJob(jobId: string): Promise<void> {
  const backend = backendUrl();
  if (!backend) return;
  try {
    await fetch(`${backend}/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
  } catch { /* ignore — frontend abort already happened */ }
}

interface StrategyProposeResult {
  pack_id: string;
  directions: StrategicDirectionDTO[];
  elapsed_s: number;
}

interface StrategyExpandResult {
  pack_id: string;
  pack: StrategyPackDTO;
  topicgen_errors?: string[];
  scheduler_error?: string;
  resourcer_error?: string;
  topic_candidate_count?: number;
  elapsed_s: number;
}

export const api = {
  isConnected: () => !!backendUrl(),

  async health(): Promise<{ ok: boolean }> {
    const backend = backendUrl();
    if (!backend) return { ok: false };
    try {
      const res = await fetch(`${backend}/api/health`);
      return await res.json();
    } catch {
      return { ok: false };
    }
  },

  status: () =>
    getJson<Status>("/api/status", "status_static.json").catch(() => null),

  // DNA -----------------
  dnaLatest: () => getJson<DnaArtifact>("/api/dna/latest", "dna_latest.json"),
  dnaVersions: () =>
    getJson<{ version: string; created_at: number; summary: any }[]>(
      "/api/dna/versions", "dna_versions.json"
    ),

  // Libraries -----------------
  libraries: () => getJson<Library[]>("/api/libraries", "libraries.json"),
  platforms: async (): Promise<Platform[]> => {
    const backend = backendUrl();
    if (!backend) return STATIC_PLATFORMS;
    try {
      const res = await fetch(`${backend}/api/platforms`);
      if (!res.ok) return STATIC_PLATFORMS;
      return res.json();
    } catch { return STATIC_PLATFORMS; }
  },
  uploadLibrary: async (file: File, displayName: string, platform: string | "auto" = "auto"): Promise<Library> => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "上传库需要本地后端");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("display_name", displayName);
    fd.append("platform", platform);
    const res = await fetch(`${backend}/api/libraries/upload`, {
      method: "POST", body: fd,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new HttpError(res.status, `upload → ${res.status}: ${text.slice(0, 400)}`);
    }
    return res.json();
  },
  importLibrary: async (file: File, displayName: string, platform: string | "auto" = "auto"): Promise<{
    lib_id: string; platform: string; notes_count: number; size_bytes: number;
    detected_platform?: string | null;
    schema_warnings?: string[];
    dna_version?: string;
    analyzed?: boolean;
    analyze_error?: string;
    section_errors?: Record<string, string>;
    promote_warning?: string;
    adapter?: {
      adapted: boolean;
      reason?: string;
      notes_rows?: number;
      source_tables?: string[];
      mapping_summary?: any;
      view_error?: string;
    };
    adapter_error?: string;
  }> => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "导入需要本地后端");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("display_name", displayName);
    fd.append("platform", platform);
    fd.append("activate", "1");
    fd.append("analyze", "1");
    const res = await fetch(`${backend}/api/libraries/import`, {
      method: "POST", body: fd,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new HttpError(res.status, `import → ${res.status}: ${text.slice(0, 400)}`);
    }
    return res.json();
  },
  setLibraryPlatform: (libId: string, platform: string) =>
    postJson<{ lib_id: string; platform: string }>(`/api/libraries/${libId}/platform`, { platform }),
  activateLibrary: (libId: string) => postJson<{ active: string }>(`/api/libraries/${libId}/activate`, {}),
  deleteLibrary: async (libId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "删除库需要本地后端");
    const res = await fetch(`${backend}/api/libraries/${libId}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },
  analyzeLibrary: (libId: string) => postJson<any>(`/api/libraries/${libId}/analyze`, {}),

  // RAG -----------------
  ragSearch: (q: string, k = 6, n = 10) =>
    getJson<any>(`/api/rag/search?q=${encodeURIComponent(q)}&k=${k}&n=${n}`),

  // Drafts -----------------
  drafts: () => getJson<DraftListItem[]>("/api/drafts", "drafts.json"),
  draftDetail: (id: string) =>
    getJson<DraftDetail>(`/api/drafts/${id}`, `drafts/${id}.json`),
  scoreCandidate: (draftId: string, candidateId: string, score: number) =>
    postJson(`/api/drafts/${draftId}/candidates/${candidateId}/score`, { score }),
  chooseCandidate: (draftId: string, candidateId: string) =>
    postJson(`/api/drafts/${draftId}/candidates/${candidateId}/choose`, {}),

  // v0.61.27 ：历史出稿删除
  deleteDraft: async (draftId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const r = await fetch(`${backend}/api/drafts/${draftId}`, { method: "DELETE" });
    if (!r.ok) throw new HttpError(r.status, await r.text().catch(() => ""));
    return r.json() as Promise<{ deleted: string; rows: Record<string, number> }>;
  },

  // v0.61.27 ：变现评估 — 商单适合度 + 风险 + 价位 + 改进建议
  evaluateMonetization: (draftId: string, req?: {
    candidate_id?: string;
    monetization_intent?: "none" | "soft_lead" | "hard_sell" | "brand_collab";
    model_spec?: string;
  }) => postJson<{
    commercial_score: number;
    factors: Record<string, number>;
    estimated_price_band: string;
    verdict: string;
    risks: string[];
    suggestions: string[];
  }>(`/api/drafts/${draftId}/monetization/eval`, req ?? {}),

  // v0.61.27 ：生成私域引流话术（评论 / 私信开场 / 主页简介）
  generateLeadScripts: (draftId: string, req?: {
    candidate_id?: string;
    model_spec?: string;
  }) => postJson<{
    comment_prompts: string[];
    dm_opener: string[];
    bio_oneliner: string;
  }>(`/api/drafts/${draftId}/monetization/lead_scripts`, req ?? {}),

  // v0.61.26: 跨平台改稿。把一条 final draft 改成 target_platform 版本。
  // 可选 target_lib_id ：如果用户上传过该平台的 lib，会拉真实爆款做 voice 锚。
  repurposeDraft: (draftId: string, req: {
    target_platform: string;
    target_lib_id?: string | null;
    model_spec?: string;
  }, signal?: AbortSignal) => postJson<{
    child_draft_id: string;
    candidate_id: string;
    target_platform: string;
    elapsed_s: number;
    payload: any;
    rationale: string;
  }>(`/api/drafts/${draftId}/repurpose`, req, signal),

  // Retrospective ----------------------------------------------------------
  markPublished: (draftId: string, body: {
    published_title?: string | null;
    published_body?: string | null;
    published_url?: string | null;
    published_notes?: string | null;
  }) => postJson<{ draft_id: string; published_at: number }>(
    `/api/drafts/${draftId}/publish`, body),
  unmarkPublished: async (draftId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/drafts/${draftId}/publish`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },
  recordDraftPerformance: (draftId: string, body: {
    likes?: number | null; comments?: number | null; saves?: number | null;
    shares?: number | null; views?: number | null; follower_delta?: number | null;
    notes?: string;
  }) => postJson<{ perf_id: string; recorded_at: number }>(
    `/api/drafts/${draftId}/performance`, body),
  listPublishedDrafts: (libraryId?: string) =>
    getJson<any[]>(`/api/retrospective/published${libraryId ? `?library_id=${libraryId}` : ""}`)
      .catch(() => []),
  runRetrospective: (body: {
    draft_ids?: string[]; library_id?: string | null; model_spec?: string;
  }, signal?: AbortSignal) => postJson<{ review_id: string; elapsed_s: number; analysis: any; draft_ids: string[] }>(
    "/api/retrospective/analyze", body, signal),
  listRetrospectives: (libraryId?: string) =>
    getJson<any[]>(`/api/retrospective/reviews${libraryId ? `?library_id=${libraryId}` : ""}`)
      .catch(() => []),
  getRetrospective: (reviewId: string) =>
    getJson<any>(`/api/retrospective/reviews/${reviewId}`),

  // Projects ----------------
  listProjects: (includeArchived = false) =>
    getJson<{ projects: ProjectDTO[]; active: string }>(`/api/projects?include_archived=${includeArchived}`, "projects.json")
      .catch(() => ({ projects: [] as ProjectDTO[], active: "default" })),
  createProject: (name: string, description = "", emoji = "📁") =>
    postJson<{ project_id: string; name: string; emoji: string; description: string }>(
      "/api/projects", { name, description, emoji }
    ),
  activateProject: (projectId: string) =>
    postJson<{ active: string }>(`/api/projects/${projectId}/activate`, {}),
  archiveProject: async (projectId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/projects/${projectId}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },
  hardDeleteProject: async (projectId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/projects/${projectId}?hard=true`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json() as Promise<{ deleted: string; rows: Record<string, number> }>;
  },
  patchProject: async (projectId: string, body: { name?: string; description?: string; emoji?: string }) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/projects/${projectId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: body.name ?? "", description: body.description ?? "", emoji: body.emoji ?? "📁",
      }),
    });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },

  // Insight (Claude × OpenAI report) -----------
  // mode='fast' (default) Sonnet × 2, ~60-80 s. mode='deep' = Opus full pipeline, ~200-250 s.
  runInsight: (libraryId: string, opts?: { mode?: "fast" | "deep"; claude_spec?: string; openai_spec?: string; moderator_spec?: string }, signal?: AbortSignal) =>
    postJson<InsightReportDTO>("/api/insight/run", { library_id: libraryId, ...opts }, signal),
  listInsights: (libraryId?: string) =>
    getJson<{ report_id: string; library_id: string; created_at: number; status: string; elapsed_s: number | null }[]>(
      `/api/insight${libraryId ? `?library_id=${libraryId}` : ""}`,
      "insights.json"
    ).catch(() => []),
  getInsight: (reportId: string) => getJson<InsightReportDTO>(`/api/insight/${reportId}`),

  // External reports (user-uploaded) -------------------
  uploadExternalReport: (req: {
    name: string; content: string;
    library_id?: string | null; source?: string; format?: string;
  }) => postJson<{
    report_id: string; name: string; source: string; format: string;
    library_id: string | null; uploaded_at: number; content_chars: number;
  }>("/api/external_reports", req),
  uploadExternalReportFile: async (file: File, name?: string, libraryId?: string | null): Promise<{
    report_id: string; name: string; source: string; format: string;
    library_id: string | null; uploaded_at: number; content_chars: number;
    extract_warning?: string;
  }> => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "上传需要本地后端");
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    if (libraryId) fd.append("library_id", libraryId);
    const res = await fetch(`${backend}/api/external_reports/upload_file`, {
      method: "POST", body: fd,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new HttpError(res.status, `upload_file → ${res.status}: ${text.slice(0, 400)}`);
    }
    return res.json();
  },
  listExternalReports: (libraryId?: string) =>
    // v0.61.6 ：去掉 .catch(() => [])。这个 silent fallback 让任何 API/网络/
    // 解析 error 都变成「externals 永远 0 长」，结果用户看不到整合按钮 +
    // 下一步按钮，还以为是 UI bug。现在让错误真正抛出，调用方用 allSettled
    // 包，错误进 console + 用户看到 banner。
    getJson<{
      report_id: string; library_id: string | null; name: string;
      source: string; format: string; content_chars: number; uploaded_at: number;
    }[]>(`/api/external_reports${libraryId ? `?library_id=${libraryId}` : ""}`),
  getExternalReport: (id: string) =>
    getJson<{ report_id: string; name: string; content: string; format: string; source: string; library_id: string | null; uploaded_at: number }>(`/api/external_reports/${id}`),
  deleteExternalReport: async (id: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/external_reports/${id}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },
  integrateExternalReports: (req: {
    source_ids: string[]; library_id?: string | null;
    include_consensus_report_id?: string | null;
    model_spec?: string;
  }, signal?: AbortSignal) => postJson<{
    integrated_id: string; status: string; elapsed_s: number;
    source_ids: string[]; source_names: string[];
    consensus: any;  // same shape as insight consensus
  }>("/api/external_reports/integrate", req, signal),
  listIntegratedReports: (libraryId?: string) =>
    // v0.61.6 ：去 silent .catch(() => []) — 让真错误冒泡。
    getJson<{
      integrated_id: string; library_id: string | null; created_at: number;
      status: string; source_ids: string[]; elapsed_s: number | null; error: string | null;
    }[]>(`/api/integrated_reports${libraryId ? `?library_id=${libraryId}` : ""}`),
  getIntegratedReport: (id: string) =>
    getJson<{
      integrated_id: string; library_id: string | null; created_at: number;
      status: string; source_ids: string[]; elapsed_s: number; consensus: any;
      /** v0.61.11: 用户勾选采纳的 single_side_views 索引。 */
      included_single_side_view_indices?: number[];
    }>(`/api/integrated_reports/${id}`),
  /** v0.61.11: 保存用户对 single_side_views 的「采纳」勾选。Down-stream
   *  Strategy / Composer 会把这些观点作为强参考注入 prompt。 */
  setIntegratedSingleSideInclusion: (integratedId: string, indices: number[]) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    return fetch(
      `${backend}/api/integrated_reports/${integratedId}/single_side_inclusion`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ indices }),
      },
    ).then(async r => {
      if (!r.ok) throw new HttpError(r.status, await r.text().catch(() => ""));
      return r.json() as Promise<{ updated: boolean; indices: number[] }>;
    });
  },

  // Strategy -----------------
  autofillStrategy: (opts?: { personal_hint?: string; constraints_hint?: string;
                              deep?: boolean;
                              claude_spec?: string; openai_spec?: string;
                              moderator_spec?: string }, signal?: AbortSignal) =>
    postJson<{
      input: AccountInputDTO;
      field_rationale: Record<string, { source: string; rationale: string; alternatives?: any[] }>;
      consensus_notes: string[];
      single_side_views: { side: string; field: string; point: string; note?: string }[];
      claude_proposal: any;
      openai_proposal: any;
      elapsed_s: number;
    }>("/api/strategy/autofill", opts ?? {}, signal),
  proposeStrategy: (req: Partial<AccountInputDTO> & { positioning: string; target_audience: string; positioner_spec?: string }, signal?: AbortSignal) =>
    postJson<StrategyProposeResult>("/api/strategy/propose", req, signal),

  /** Streaming variant of proposeStrategy.
   * onEvent fires for each SSE event ('delta' / 'progress' / 'complete' / 'error').
   * Returns the final StrategyProposeResult (resolved from the 'complete' event)
   * or throws on 'error'. */
  proposeStrategyStream: async (
    req: Partial<AccountInputDTO> & { positioning: string; target_audience: string; positioner_spec?: string },
    onEvent: (kind: string, data: any) => void,
    signal?: AbortSignal,
  ): Promise<StrategyProposeResult> => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/strategy/propose/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(req),
      signal,
    });
    if (!res.ok || !res.body) {
      const t = await res.text().catch(() => "");
      throw new HttpError(res.status, `propose stream → ${res.status}: ${t.slice(0, 400)}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let result: StrategyProposeResult | null = null;
    let lastError: string | null = null;

    // Parse SSE: events delimited by blank line; lines 'event: X' + 'data: Y'.
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // Pull complete event blocks (delimited by \n\n).
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let kind = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) kind = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        const dataStr = dataLines.join("\n");
        let data: any = dataStr;
        try { data = JSON.parse(dataStr); } catch { /* keep raw */ }
        onEvent(kind, data);
        if (kind === "complete") result = data as StrategyProposeResult;
        if (kind === "error") lastError = data?.message ?? "stream error";
      }
    }
    if (lastError) throw new HttpError(500, lastError);
    if (!result) throw new HttpError(500, "stream ended without complete event");
    return result;
  },
  // v0.59: chosenIdx kept as legacy single-direction param; new chosenIdxs is
  // the multi-direction array. Backend reads chosen_direction_idxs first,
  // falls back to chosen_direction_idx for old callers.
  expandStrategy: (packId: string, chosenIdx: number, opts?: {
    topicgen_spec?: string; scheduler_spec?: string; resourcer_spec?: string;
    restart?: boolean; chosenIdxs?: number[];
  }, signal?: AbortSignal) =>
    postJson<StrategyExpandResult>(`/api/strategy/${packId}/expand`, {
      chosen_direction_idx: chosenIdx,
      chosen_direction_idxs: opts?.chosenIdxs ?? [],
      topicgen_spec: opts?.topicgen_spec, scheduler_spec: opts?.scheduler_spec,
      resourcer_spec: opts?.resourcer_spec, restart: opts?.restart,
    }, signal),
  // v0.59: 起号目标分类（GoalPicker step 用）
  listStrategyGoals: () =>
    getJson<GoalTypeDTO[]>("/api/strategy/goals").catch(() => []),
  listStrategies: () => getJson<StrategyListItem[]>("/api/strategy", "strategies.json").catch(() => [] as StrategyListItem[]),
  getStrategy: (packId: string) => getJson<StrategyDetail>(`/api/strategy/${packId}`),
  deleteStrategy: async (packId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/strategy/${packId}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },

  // Iteration loop — feed performance back, iterate next cycle ------------
  saveStrategyPerformance: (packId: string, body: {
    raw_notes?: string;
    per_slot?: { slot_idx: number; [k: string]: any }[];
    overall?: Record<string, any>;
  }) => postJson<{ feedback_id: string; created_at: number; per_slot: any[]; overall: any; raw_notes: string }>(
    `/api/strategy/${packId}/performance`, body),
  listStrategyPerformance: (packId: string) =>
    getJson<{ feedback_id: string; created_at: number; raw_notes: string; per_slot: any[]; overall: any }[]>(
      `/api/strategy/${packId}/performance`).catch(() => []),
  iterateStrategy: (packId: string, body: { feedback_id: string; iterator_spec?: string }) =>
    postJson<{
      pack_id: string; parent_pack_id: string; iteration_n: number;
      iteration_summary: string; wins_to_double_down: any[]; losses_to_drop: any[];
      pack: StrategyPackDTO;
    }>(`/api/strategy/${packId}/iterate`, body),

  // v0.62.13 ：AI 智能预填 brief — 用户在起号策略点「写这个」跳到 Composer
  // 时调这个 ：后端用 LLM 综合 pack + slot + DNA 摘要生成完整 brief，前端
  // 拿到后填表单。用户能看见 AI 推荐的内容 + 随便改。
  prefillBriefFromSlot: (packId: string, body: {
    slot_idx: number; alt_idx?: number; spec?: string;
  }) =>
    postJson<{
      topic: string; angles: string[]; target_length: number;
      cta_strength: "none" | "soft" | "strong";
      niche: string; extra_constraints: string;
      rationale?: string; _fallback?: boolean;
    }>(`/api/composer/strategy/${packId}/prefill_brief`, body),

  // v0.53 — compliance (hard redline gate) ------------------------------
  complianceCheck: (body: {
    title?: string; body?: string; tags?: string[]; cover_prompt?: string;
  }) => postJson<ComplianceCheck>("/api/compliance/check", body),
  complianceRewrite: (text: string, where: "title" | "body" = "body") =>
    postJson<{ original: string; rewritten: string; hits: ComplianceHit[]; changed: boolean }>(
      "/api/compliance/rewrite", { text, where }),
  complianceRules: () =>
    getJson<{ rule_id: string; patterns: string[]; category: string;
              severity: string; safe_alternative: string; rationale: string }[]>(
      "/api/compliance/rules").catch(() => []),
  draftCompliance: (draftId: string) =>
    getJson<{ draft_id: string; final_candidate_id: string | null;
              final_severity: "pass" | "warn" | "block";
              by_candidate: Record<string, { severity: string; hits: ComplianceHit[]; checked_at: number }> }>(
      `/api/drafts/${draftId}/compliance`),

  // v0.61.27 ：trackingRefresh / trackingHistory 已删除。原因 ：抓 xhs 后台
  // 需要 chrome131 TLS impersonation + 登录 cookie，触发风控可能 ban 账号。
  // 改纯手动填。trackingStatus 保留只为兼容性（永远返回 manual-only）。
  trackingStatus: () =>
    getJson<{ crawler_available: boolean; hint: string; manual_only?: boolean }>(
      "/api/tracking/status"
    ).catch(() => ({
      crawler_available: false, hint: "未连接后端", manual_only: true,
    })),

  // v0.53 — variants (one-click fan-out) ---------------------------------
  spawnVariants: (draftId: string, angles: string[]) =>
    postJson<{
      parent_draft_id: string; angles_requested: string[];
      variants: { angle: string; draft_id: string | null;
                  variant_label: string; compliance: ComplianceCheck | null;
                  error: string | null }[];
      succeeded: number; failed: number;
    }>(`/api/drafts/${draftId}/variants`, { angles }),
  listVariants: (draftId: string) =>
    getJson<{ draft_id: string; generated_at: number;
              variant_label: string | null; angle: string | null;
              final_title: string | null;
              published?: number; published_at?: number; published_url?: string;
              topic?: string;
              compliance_severity?: "pass" | "warn" | "block" | null }[]>(
      `/api/drafts/${draftId}/variants`).catch(() => []),

  // v0.53 — provenance (Researcher RAG refs) -----------------------------
  draftRag: (draftId: string) =>
    getJson<{ draft_id: string; refs: any[]; comments: any[]; hooks: any[]; has_data: boolean }>(
      `/api/drafts/${draftId}/rag`),

  // v0.53 — feedback (prompt-version proposals + aggregate rollup) -------
  feedbackProposeFromReview: (reviewId: string, opts?: { generator_name?: string; proposer_spec?: string }) =>
    postJson<{
      skipped: boolean; reason?: string;
      proposal_id?: string; review_id: string;
      parent_version?: string; proposed_version?: string;
      diff_summary?: string; expected_gain?: string;
      evidence?: any[]; created_at?: number; status?: string;
    }>("/api/feedback/propose-from-review", {
      review_id: reviewId,
      generator_name: opts?.generator_name ?? "title_body_gen",
      proposer_spec: opts?.proposer_spec ?? "openai:gpt-4o",
    }),
  listProposals: (status?: "pending" | "approved" | "rejected") =>
    getJson<PromptProposal[]>(`/api/feedback/proposals${status ? `?status=${status}` : ""}`).catch(() => []),
  getProposal: (proposalId: string) =>
    getJson<PromptProposal>(`/api/feedback/proposals/${proposalId}`),
  approveProposal: (proposalId: string, notes = "") =>
    postJson<{ proposal_id: string; status: string; new_active_version: string; decided_at: number }>(
      `/api/feedback/proposals/${proposalId}/approve`, { notes }),
  rejectProposal: (proposalId: string, notes = "") =>
    postJson<{ proposal_id: string; status: string; decided_at: number }>(
      `/api/feedback/proposals/${proposalId}/reject`, { notes }),
  feedbackRollup: (projectId?: string) =>
    getJson<{ project_id: string; drafts: any[] }>(
      `/api/feedback/rollup${projectId ? `?project_id=${projectId}` : ""}`).catch(() => ({ project_id: "", drafts: [] })),

  // v0.58 — Product Context (project-level brand bible) -----------------
  listProductContexts: (activeOnly = false) =>
    getJson<ProductContextDTO[]>(`/api/product-context${activeOnly ? "?active_only=true" : ""}`).catch(() => []),
  getProductContext: (contextId: string) =>
    getJson<ProductContextDTO>(`/api/product-context/${contextId}`),
  createProductContext: (name: string, bodyText: string) =>
    postJson<ProductContextDTO>("/api/product-context", { name, body_text: bodyText }),
  uploadProductContextFile: async (file: File, name: string = ""): Promise<ProductContextDTO & { extract_warning?: string }> => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "上传需要本地后端");
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    const res = await fetch(`${backend}/api/product-context/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      const t = await res.text().catch(() => "");
      throw new HttpError(res.status, `upload → ${res.status}: ${t.slice(0, 400)}`);
    }
    return res.json();
  },
  deleteProductContext: async (contextId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/product-context/${contextId}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },
  setProductContextActive: (contextId: string, active: boolean) =>
    postJson<{ context_id: string; active: boolean }>(
      `/api/product-context/${contextId}/active`, { active }),

  // Compose -----------------
  compose: (req: Brief & {
    strategist_spec?: string;
    drafter_spec?: string;
    critic_spec?: string;
    refiner_spec?: string;
    synthesizer_spec?: string;
    planner_spec?: string;
    skip_strategist?: boolean;
    skip_critics?: boolean;
    skip_refiner?: boolean;
    skip_synthesizer?: boolean;
    skip_planner?: boolean;
    /** v0.61.22 ：每角度专属 model spec 覆写。{} = 全部 round-robin。 */
    angle_models?: Record<string, string>;
  }, signal?: AbortSignal) => postJson<ComposeBundle>("/api/compose", req, signal),
};

export { HttpError, STATIC_PLATFORMS };
