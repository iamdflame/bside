import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type JudgeInfo } from "../lib/api";
import { relTime } from "../lib/format";
import "./judge.css";

/** Zero-friction judge surface: live health, one-click fresh run, restore proof. */
export function JudgePage() {
  const [info, setInfo] = useState<JudgeInfo | null>(null);
  const [running, setRunning] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restored, setRestored] = useState<{ shows: number; episodes: number; ms: number } | null>(null);
  const [error, setError] = useState("");
  const nav = useNavigate();

  const refresh = useCallback(() => {
    api.judge().then(setInfo).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 6000);
    return () => clearInterval(t);
  }, [refresh]);

  const runFresh = async () => {
    setError("");
    setRunning(true);
    try {
      const r = await api.judgeRun();
      nav(`/ep/${r.episode_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      setRunning(false);
    }
  };

  const restore = async () => {
    setRestoring(true);
    setRestored(null);
    try {
      setRestored(await api.restore());
      refresh();
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div className="container judge">
      <header className="rise">
        <p className="caps" style={{ color: "var(--signal-bright)" }}>
          Judge mode — no sign-in, nothing staged
        </p>
        <h1 className="display judge__title">See it run for real.</h1>
        <p className="judge__sub">
          Everything below hits live services: AssemblyAI transcription, LLM direction, FLUX image
          generation, ffmpeg composition — orchestrated by Genblaze, archived to a private
          Backblaze B2 bucket with hash-verified manifests you can re-check from the UI.
        </p>
      </header>

      <div className="judge__grid">
        <section className="card judge__panel rise" style={{ animationDelay: "60ms" }}>
          <h2 className="caps">1 · Run a fresh episode</h2>
          <p className="judge__text">
            One click processes the bundled demo episode through the entire real pipeline — new
            B2 objects, new manifests, fresh timestamps. Takes about two minutes; you'll watch
            every stage live.
          </p>
          <button className="btn btn--signal" onClick={runFresh} disabled={running || !info?.fixture_available}>
            {running ? "Starting…" : "Run the pipeline now →"}
          </button>
          {error && <p className="judge__err">{error}</p>}
          {info && !info.fixture_available && (
            <p className="judge__err">Demo fixture missing in this deployment.</p>
          )}
        </section>

        <section className="card judge__panel rise" style={{ animationDelay: "120ms" }}>
          <h2 className="caps">2 · Or inspect a finished kit</h2>
          {info === null ? (
            <div className="skeleton" style={{ height: 90 }} />
          ) : info.episodes.length === 0 ? (
            <p className="judge__text">No episodes yet — run one fresh, or restore from B2 below.</p>
          ) : (
            <ul className="judge__eps">
              {info.episodes.slice(0, 5).map((e) => (
                <li key={e.id}>
                  <Link to={`/ep/${e.id}`} className="judge__ep">
                    <span className={`chip ${e.status === "sealed" ? "chip--done" : e.status === "failed" ? "chip--failed" : "chip--running"}`}>
                      {e.status.replace("_", " ")}
                    </span>
                    <span className="judge__eptitle">{e.title}</span>
                    <span className="caps num">{relTime(e.updated_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card judge__panel rise" style={{ animationDelay: "180ms" }}>
          <h2 className="caps">3 · Prove B2 is the system of record</h2>
          <p className="judge__text">
            This instance's local database is disposable. Press restore and the entire application
            state — shows, episodes, review decisions, lineage — rebuilds from the B2 bucket alone.
          </p>
          <button className="btn" onClick={restore} disabled={restoring}>
            {restoring ? "Walking the bucket…" : "Restore state from B2"}
          </button>
          {restored && (
            <p className="judge__restored num rise" role="status">
              ✓ rebuilt {restored.shows} show{restored.shows === 1 ? "" : "s"} ·{" "}
              {restored.episodes} episode{restored.episodes === 1 ? "" : "s"} in {restored.ms} ms
            </p>
          )}
        </section>

        <section className="card judge__panel rise" style={{ animationDelay: "240ms" }}>
          <h2 className="caps">Live integration health</h2>
          {info === null ? (
            <div className="skeleton" style={{ height: 90 }} />
          ) : (
            <ul className="health">
              {Object.entries(info.providers).map(([name, on]) => (
                <li key={name} className="health__row">
                  <span className={`health__dot ${on ? "health__dot--on" : ""}`} aria-hidden />
                  <span className="health__name">{PROVIDER_LABEL[name] ?? name}</span>
                  <span className="caps num">
                    {info.breakers[breakerKey(name)] ?? (on ? "ready" : "not configured")}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {info && (
            <p className="judge__limits caps num">
              guardrails: {info.limits.daily_episodes} episodes/day · {info.limits.max_audio_minutes}
              min audio cap
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  b2: "Backblaze B2 (archive of record)",
  assemblyai: "AssemblyAI (transcription)",
  gemini: "Google Gemini (direction · image fallback)",
  nvidia: "NVIDIA NIM (FLUX art · chat fallback)",
  elevenlabs: "ElevenLabs (demo narration)",
};

function breakerKey(provider: string): string {
  return { gemini: "gemini-chat", nvidia: "nvidia" }[provider] ?? provider;
}
