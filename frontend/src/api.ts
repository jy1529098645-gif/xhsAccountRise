import type {
  Brief, ComposeBundle, DnaArtifact, DraftDetail, DraftListItem,
  Library, Platform, Status,
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
const STATIC_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/data`;

export function backendUrl(): string {
  return localStorage.getItem(KEY) || "";
}
export function setBackendUrl(url: string) {
  if (url) localStorage.setItem(KEY, url.replace(/\/$/, ""));
  else localStorage.removeItem(KEY);
}

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
  uploadLibrary: async (file: File, displayName: string, platform = "xiaohongshu"): Promise<Library> => {
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
