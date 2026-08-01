import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { EvidenceDrawer } from "../components/Evidence";
import {
  api,
  mediaUrl,
  subscribeEvents,
  type BsideEvent,
  type Episode,
  type KitAsset,
} from "../lib/api";
import { fmtBytes, fmtDur, fmtTime, relTime } from "../lib/format";
import "./episode.css";

const STAGE_LABEL: Record<string, string> = {
  ingest: "Ingest",
  transcribe: "Transcribe",
  direct: "Direct",
  art: "Art",
  cards: "Quote cards",
  audiograms: "Audiograms",
  seal: "Seal",
};

const STAGE_BLURB: Record<string, string> = {
  ingest: "Source audio hashed, manifested, archived to B2",
  transcribe: "AssemblyAI · word-level timings",
  direct: "LLM reads the transcript, writes the kit direction",
  art: "FLUX / Gemini render the cover under quality gates",
  cards: "Deterministic typography over the art",
  audiograms: "ffmpeg composition · karaoke captions",
  seal: "Fetched-byte verification, ZIP to the archive",
};

export function EpisodePage() {
  const { epId = "" } = useParams();
  const [ep, setEp] = useState<Episode | null>(null);
  const [live, setLive] = useState(false);
  const [feed, setFeed] = useState<BsideEvent[]>([]);
  const [evidenceFor, setEvidenceFor] = useState<KitAsset | null>(null);
  const refreshTimer = useRef<number>();

  const refresh = useCallback(() => {
    api.episode(epId).then(setEp).catch(() => undefined);
  }, [epId]);

  useEffect(() => {
    refresh();
    const unsub = subscribeEvents(epId, (e) => {
      setLive(true);
      setFeed((prev) => [...prev.slice(-120), e]);
      window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(refresh, 350);
    });
    return () => {
      unsub();
      window.clearTimeout(refreshTimer.current);
    };
  }, [epId, refresh]);

  const review = async (asset: KitAsset, decision: "approved" | "rejected", feedback = "") => {
    await api.review(epId, asset.id, decision, feedback);
    refresh();
  };

  const kit = useMemo(() => {
    if (!ep) return { art: [], cards: [], audiograms: [] };
    return {
      art: ep.assets.filter((a) => a.kind === "episode_art"),
      cards: ep.assets.filter((a) => a.kind === "quote_card"),
      audiograms: ep.assets.filter((a) => a.kind === "audiogram"),
    };
  }, [ep]);

  if (!ep) {
    return (
      <div className="container ep">
        <div className="skeleton" style={{ height: 68, maxWidth: 560 }} />
        <div className="ep__layout" style={{ marginTop: "var(--s5)" }}>
          <div className="skeleton" style={{ height: 420 }} />
          <div className="skeleton" style={{ height: 420 }} />
        </div>
      </div>
    );
  }

  const failedStage = ep.stages.find((s) => s.status === "failed");

  return (
    <div className="container ep">
      <header className="ep__head rise">
        <div>
          <div className="ep__status">
            <span className={`chip ${statusChip(ep.status)}`}>{ep.status.replace("_", " ")}</span>
            {live && <span className="chip chip--muted"><span className="livedot" aria-hidden />live</span>}
            <span className="caps num">{relTime(ep.updated_at)}</span>
          </div>
          <h1 className="display ep__title">{ep.title}</h1>
          <p className="ep__meta caps num">
            {ep.source.filename} · {fmtBytes(ep.source.size_bytes)}
            {ep.source.duration_s ? ` · ${fmtTime(ep.source.duration_s)}` : ""}
            {ep.word_count ? ` · ${ep.word_count} words` : ""}
          </p>
        </div>
        <div className="ep__actions">
          {ep.status === "failed" && (
            <button className="btn btn--signal" onClick={() => api.retry(epId).then(refresh)}>
              Retry pipeline
            </button>
          )}
          {ep.release_key && <ReleaseButton epId={epId} version={ep.release_version} />}
        </div>
      </header>

      {ep.status === "in_review" && (
        <div className="ep__banner rise" role="status">
          <strong>Your call:</strong>&nbsp;review each asset below — approve what ships, reject
          with a note to regenerate. The kit seals when every asset has your decision.
        </div>
      )}
      {failedStage && ep.status === "failed" && (
        <div className="ep__banner ep__banner--fail rise" role="alert">
          <strong>{STAGE_LABEL[failedStage.name]} failed</strong>&nbsp;after{" "}
          {failedStage.attempts} attempt{failedStage.attempts === 1 ? "" : "s"}:{" "}
          <code className="ep__err">{failedStage.error}</code>
        </div>
      )}

      <div className="ep__layout">
        {/* -------- pipeline rail -------- */}
        <aside className="rail card">
          <h2 className="caps rail__head">Pipeline</h2>
          <ol className="rail__list">
            {ep.stages.map((s) => (
              <li key={s.name} className={`stage stage--${s.status}`}>
                <span className="stage__dot" aria-hidden />
                <div className="stage__body">
                  <div className="stage__row">
                    <span className="stage__name">{STAGE_LABEL[s.name] ?? s.name}</span>
                    <span className="stage__time caps num">
                      {s.status === "running" ? "running" : fmtDur(s.duration_s)}
                    </span>
                  </div>
                  <p className="stage__blurb">{STAGE_BLURB[s.name]}</p>
                  {s.provider_notes.slice(-2).map((n, i) => (
                    <p key={i} className="stage__note num">
                      {n}
                    </p>
                  ))}
                  {s.error && s.status === "failed" && (
                    <p className="stage__errnote">{s.error.slice(0, 160)}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
          <div className="rail__feed">
            <h3 className="caps rail__head">Signal</h3>
            <ul className="feed">
              {feed.slice(-7).reverse().map((e) => (
                <li key={e.seq} className="feed__item num">
                  <span className="feed__type">{e.type}</span>
                  <span className="feed__ts">{e.ts.slice(11, 19)}</span>
                </li>
              ))}
              {feed.length === 0 && <li className="feed__item">listening…</li>}
            </ul>
          </div>
        </aside>

        {/* -------- kit -------- */}
        <div className="kit">
          {ep.direction && (
            <section className="card panel rise">
              <h2 className="caps panel__head">Direction</h2>
              <div className="direction">
                <div>
                  <h3 className="caps direction__label">Titles</h3>
                  <ul className="direction__titles display">
                    {ep.direction.titles.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="caps direction__label">Chapters</h3>
                  <ol className="chapters num">
                    {ep.direction.chapters.map((c) => (
                      <li key={c.start}>
                        <span className="chapters__ts">{fmtTime(c.start)}</span> {c.title}
                      </li>
                    ))}
                  </ol>
                </div>
                <div className="direction__summary">
                  <h3 className="caps direction__label">Summary</h3>
                  <p>{ep.direction.summary}</p>
                </div>
              </div>
            </section>
          )}

          {(kit.art.length > 0 || ep.status === "processing") && (
            <section className="card panel rise">
              <h2 className="caps panel__head">Episode art</h2>
              <div className="artrow">
                {kit.art.map((a) => (
                  <AssetFigure
                    key={a.id}
                    asset={a}
                    epId={epId}
                    onReview={review}
                    onEvidence={() => setEvidenceFor(a)}
                  >
                    <img
                      src={mediaUrl(a.b2_key)}
                      alt={`Episode art generation ${a.generation}`}
                      className="artrow__img"
                      loading="lazy"
                    />
                  </AssetFigure>
                ))}
                {kit.art.length === 0 && <SkeletonTile label="rendering…" />}
              </div>
            </section>
          )}

          {(kit.cards.length > 0 || kit.audiograms.length > 0) && ep.direction && (
            <section className="card panel rise">
              <h2 className="caps panel__head">Quotes · cards · audiograms</h2>
              <div className="quotes">
                {ep.direction.quotes.map((q) => {
                  const card = kit.cards.filter((c) => c.quote_id === q.id);
                  const agram = kit.audiograms.filter((c) => c.quote_id === q.id);
                  return (
                    <article key={q.id} className="quote">
                      <blockquote className="quote__text display">
                        “{q.text}”
                        <span className="quote__ts caps num">
                          {fmtTime(q.start)}–{fmtTime(q.end)}
                        </span>
                      </blockquote>
                      {q.reason && <p className="quote__reason">{q.reason}</p>}
                      <div className="quote__media">
                        {card.map((a) => (
                          <AssetFigure
                            key={a.id}
                            asset={a}
                            epId={epId}
                            onReview={review}
                            onEvidence={() => setEvidenceFor(a)}
                          >
                            <img
                              src={mediaUrl(a.b2_key)}
                              alt={`Quote card: ${q.text.slice(0, 40)}`}
                              loading="lazy"
                            />
                          </AssetFigure>
                        ))}
                        {agram.map((a) => (
                          <AssetFigure
                            key={a.id}
                            asset={a}
                            epId={epId}
                            onReview={review}
                            onEvidence={() => setEvidenceFor(a)}
                          >
                            <video
                              src={mediaUrl(a.b2_key)}
                              controls
                              preload="metadata"
                              playsInline
                            />
                          </AssetFigure>
                        ))}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          )}

          {ep.direction?.show_notes_md && (
            <section className="card panel rise">
              <h2 className="caps panel__head">Show notes</h2>
              <pre className="notes">{ep.direction.show_notes_md}</pre>
            </section>
          )}
        </div>
      </div>

      {evidenceFor && (
        <EvidenceDrawer epId={epId} asset={evidenceFor} onClose={() => setEvidenceFor(null)} />
      )}
    </div>
  );
}

function statusChip(status: Episode["status"]): string {
  return {
    created: "chip--pending",
    processing: "chip--running",
    in_review: "chip--pending",
    sealed: "chip--done",
    failed: "chip--failed",
  }[status];
}

function ReleaseButton({ epId, version }: { epId: string; version: number }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="btn btn--signal"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const r = await api.release(epId);
          window.open(r.url, "_blank", "noopener");
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Signing…" : `Download kit v${version} ↓`}
    </button>
  );
}

function SkeletonTile({ label }: { label: string }) {
  return (
    <div className="skeleton asset-skel">
      <span className="caps">{label}</span>
    </div>
  );
}

function AssetFigure({
  asset,
  epId: _epId,
  children,
  onReview,
  onEvidence,
}: {
  asset: KitAsset;
  epId: string;
  children: React.ReactNode;
  onReview: (a: KitAsset, d: "approved" | "rejected", feedback?: string) => Promise<void>;
  onEvidence: () => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState<"approved" | "rejected" | null>(null);

  const act = async (d: "approved" | "rejected") => {
    setBusy(d);
    try {
      await onReview(asset, d, feedback);
      setRejecting(false);
      setFeedback("");
    } finally {
      setBusy(null);
    }
  };

  return (
    <figure className={`asset asset--${asset.review}`}>
      <div className="asset__media">{children}</div>
      <figcaption className="asset__bar">
        <span className={`chip ${reviewChip(asset.review)}`}>
          {asset.review}
          {asset.generation > 1 ? ` · gen ${asset.generation}` : ""}
        </span>
        <span className="asset__prov caps num" title={`${asset.provider} · ${asset.model}`}>
          {asset.provider}
        </span>
        <button className="asset__verify" onClick={onEvidence} title="Provenance & verification">
          ⛨ verify
        </button>
      </figcaption>
      {asset.review === "pending" && (
        <div className="asset__review">
          {!rejecting ? (
            <>
              <button className="btn btn--signal" disabled={busy !== null} onClick={() => act("approved")}>
                {busy === "approved" ? "…" : "Approve"}
              </button>
              <button className="btn btn--danger" disabled={busy !== null} onClick={() => setRejecting(true)}>
                Reject
              </button>
            </>
          ) : (
            <>
              <input
                autoFocus
                className="field asset__feedback"
                placeholder="What should change? This drives the regeneration."
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && feedback.trim() && act("rejected")}
              />
              <button
                className="btn btn--danger"
                disabled={!feedback.trim() || busy !== null}
                onClick={() => act("rejected")}
              >
                {busy === "rejected" ? "…" : "Reject & regenerate"}
              </button>
            </>
          )}
        </div>
      )}
      {asset.review === "rejected" && asset.review_feedback && (
        <p className="asset__rejnote">“{asset.review_feedback}”</p>
      )}
    </figure>
  );
}

function reviewChip(r: KitAsset["review"]): string {
  return { pending: "chip--pending", approved: "chip--done", rejected: "chip--failed" }[r];
}
