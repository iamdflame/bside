/* Typed API client + live episode subscription (SSE). */

export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";
export type ReviewState = "pending" | "approved" | "rejected";

export interface StageRecord {
  name: string;
  status: StageStatus;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  run_ids: string[];
  manifest_keys: string[];
  provider_notes: string[];
  cost_usd: number | null;
  duration_s: number | null;
}

export interface Quote {
  id: string;
  text: string;
  start: number;
  end: number;
  reason: string;
}

export interface Chapter {
  title: string;
  start: number;
  summary: string;
}

export interface Direction {
  titles: string[];
  summary: string;
  show_notes_md: string;
  chapters: Chapter[];
  quotes: Quote[];
  art_brief: string;
  palette: string[];
}

export interface KitAsset {
  id: string;
  kind:
    | "source_audio"
    | "transcript"
    | "direction"
    | "episode_art"
    | "quote_card"
    | "audiogram"
    | "release_zip";
  label: string;
  b2_key: string;
  sha256: string;
  size_bytes: number;
  media_type: string;
  run_id: string | null;
  parent_run_id: string | null;
  manifest_key: string | null;
  provider: string | null;
  model: string | null;
  quote_id: string | null;
  review: ReviewState;
  review_feedback: string;
  generation: number;
  created_at: string;
}

export interface Episode {
  id: string;
  show_id: string;
  title: string;
  status: "created" | "processing" | "in_review" | "sealed" | "failed";
  created_at: string;
  updated_at: string;
  source: {
    filename: string;
    media_type: string;
    size_bytes: number;
    duration_s: number | null;
    sha256: string;
    b2_key: string;
  };
  stages: StageRecord[];
  transcript_key: string;
  word_count: number;
  direction: Direction | null;
  assets: KitAsset[];
  release_key: string;
  release_version: number;
}

export interface Show {
  id: string;
  name: string;
  tagline: string;
  style_canon: string;
  palette: string[];
  created_at: string;
}

export interface VerifyResult {
  key: string;
  expected_sha256: string;
  fetched_sha256: string;
  size_bytes: number;
  match: boolean;
  fetch_ms: number;
  manifest_key: string | null;
  manifest_canonical_hash: string | null;
  run_id: string | null;
  parent_run_id: string | null;
  provider: string | null;
  model: string | null;
}

export interface JudgeInfo {
  show_id: string | null;
  episodes: { id: string; title: string; status: string; updated_at: string }[];
  fixture_available: boolean;
  providers: Record<string, boolean>;
  breakers: Record<string, string>;
  limits: { daily_episodes: number; max_audio_minutes: number };
}

export interface BsideEvent {
  seq: number;
  episode_id: string;
  type: string;
  data: Record<string, unknown>;
  ts: string;
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Record<string, unknown>>("/api/health"),
  shows: () => request<Show[]>("/api/shows"),
  show: (id: string) => request<Show>(`/api/shows/${id}`),
  createShow: (body: { name: string; tagline?: string }) =>
    request<Show>("/api/shows", { method: "POST", body: JSON.stringify(body) }),
  episodes: (showId?: string) =>
    request<Episode[]>(`/api/episodes${showId ? `?show_id=${showId}` : ""}`),
  episode: (id: string) => request<Episode>(`/api/episodes/${id}`),
  upload: async (showId: string, file: File, title: string): Promise<Episode> => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    const res = await fetch(`/api/shows/${showId}/episodes`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body.detail ?? res.statusText);
    }
    return res.json();
  },
  review: (epId: string, assetId: string, decision: ReviewState, feedback = "") =>
    request<{ ok: boolean }>(`/api/episodes/${epId}/assets/${assetId}/review`, {
      method: "POST",
      body: JSON.stringify({ decision, feedback }),
    }),
  retry: (epId: string, fromStage?: string) =>
    request<{ ok: boolean }>(`/api/episodes/${epId}/retry`, {
      method: "POST",
      body: JSON.stringify({ from_stage: fromStage ?? null }),
    }),
  verify: (epId: string, assetId: string) =>
    request<VerifyResult>(`/api/episodes/${epId}/verify/${assetId}`),
  release: (epId: string) =>
    request<{ key: string; version: number; url: string }>(`/api/episodes/${epId}/release`),
  judge: () => request<JudgeInfo>("/api/judge"),
  judgeRun: () =>
    request<{ episode_id: string; show_id: string }>("/api/judge/run", { method: "POST" }),
  restore: () => request<{ shows: number; episodes: number; ms: number }>("/api/restore", { method: "POST" }),
};

export const mediaUrl = (key: string) => `/api/media?key=${encodeURIComponent(key)}`;

/** Subscribe to an episode's live event stream. Returns unsubscribe. */
export function subscribeEvents(
  episodeId: string,
  onEvent: (e: BsideEvent) => void,
  after = 0,
): () => void {
  const src = new EventSource(`/api/episodes/${episodeId}/events?after=${after}`);
  const handler = (msg: MessageEvent) => {
    try {
      onEvent(JSON.parse(msg.data) as BsideEvent);
    } catch {
      /* keepalive */
    }
  };
  const types = [
    "episode.created",
    "episode.processing",
    "episode.in_review",
    "episode.sealing",
    "episode.done",
    "episode.retry",
    "stage.started",
    "stage.progress",
    "stage.done",
    "stage.failed",
    "asset.reviewed",
    "asset.regenerating",
    "asset.regenerated",
    "job.retry",
    "job.failed",
  ];
  for (const t of types) src.addEventListener(t, handler);
  return () => src.close();
}
