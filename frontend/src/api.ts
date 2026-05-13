import type {
  Brief, ComposeBundle, DnaArtifact, DraftDetail, DraftListItem,
  Library, Platform, Status,
  AccountInputDTO, StrategicDirectionDTO, StrategyDetail, StrategyListItem, StrategyPackDTO,
  ProjectDTO, InsightReportDTO,
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

export function backendUrl(): string {
  if (localStorage.getItem(DISABLED_KEY) === "1") return "";
  return (localStorage.getItem(KEY) || DEFAULT_BACKEND).replace(/\/$/, "");
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

async function postJson<T>(path: string, body: any): Promise<T> {
  const backend = backendUrl();
  if (!backend) throw new HttpError(0, `此操作需要本地后端，请去 Settings 配置 backend URL`);
  const res = await fetch(`${backend}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new HttpError(res.status, `${path} → ${res.status}: ${text.slice(0, 400)}`);
  }
  return res.json();
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
  runInsight: (libraryId: string, opts?: { claude_spec?: string; openai_spec?: string; moderator_spec?: string }) =>
    postJson<InsightReportDTO>("/api/insight/run", { library_id: libraryId, ...opts }),
  listInsights: (libraryId?: string) =>
    getJson<{ report_id: string; library_id: string; created_at: number; status: string; elapsed_s: number | null }[]>(
      `/api/insight${libraryId ? `?library_id=${libraryId}` : ""}`,
      "insights.json"
    ).catch(() => []),
  getInsight: (reportId: string) => getJson<InsightReportDTO>(`/api/insight/${reportId}`),

  // Strategy -----------------
  autofillStrategy: (opts?: { personal_hint?: string; constraints_hint?: string;
                              claude_spec?: string; openai_spec?: string;
                              moderator_spec?: string }) =>
    postJson<{
      input: AccountInputDTO;
      field_rationale: Record<string, { source: string; rationale: string; alternatives?: any[] }>;
      consensus_notes: string[];
      single_side_views: { side: string; field: string; point: string; note?: string }[];
      claude_proposal: any;
      openai_proposal: any;
      elapsed_s: number;
    }>("/api/strategy/autofill", opts ?? {}),
  proposeStrategy: (req: Partial<AccountInputDTO> & { positioning: string; target_audience: string; positioner_spec?: string }) =>
    postJson<StrategyProposeResult>("/api/strategy/propose", req),
  expandStrategy: (packId: string, chosenIdx: number, opts?: { topicgen_spec?: string; scheduler_spec?: string; resourcer_spec?: string }) =>
    postJson<StrategyExpandResult>(`/api/strategy/${packId}/expand`, { chosen_direction_idx: chosenIdx, ...opts }),
  listStrategies: () => getJson<StrategyListItem[]>("/api/strategy", "strategies.json").catch(() => [] as StrategyListItem[]),
  getStrategy: (packId: string) => getJson<StrategyDetail>(`/api/strategy/${packId}`),
  deleteStrategy: async (packId: string) => {
    const backend = backendUrl();
    if (!backend) throw new HttpError(0, "需要本地后端");
    const res = await fetch(`${backend}/api/strategy/${packId}`, { method: "DELETE" });
    if (!res.ok) throw new HttpError(res.status, await res.text());
    return res.json();
  },

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
  }) => postJson<ComposeBundle>("/api/compose", req),
};

export { HttpError, STATIC_PLATFORMS };
