import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Episode, type Show } from "../lib/api";
import { fmtBytes, relTime } from "../lib/format";
import "./show.css";

const STATUS_CHIP: Record<Episode["status"], string> = {
  created: "chip--pending",
  processing: "chip--running",
  in_review: "chip--pending",
  sealed: "chip--done",
  failed: "chip--failed",
};

export function ShowPage() {
  const { showId = "" } = useParams();
  const [show, setShow] = useState<Show | null>(null);
  const [episodes, setEpisodes] = useState<Episode[] | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const nav = useNavigate();

  const refresh = useCallback(() => {
    api.show(showId).then(setShow).catch(() => nav("/"));
    api.episodes(showId).then(setEpisodes).catch(() => setEpisodes([]));
  }, [showId, nav]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 8000);
    return () => clearInterval(t);
  }, [refresh]);

  const handleFile = async (file: File) => {
    setError("");
    if (!file.type.startsWith("audio/")) {
      setError("That's not an audio file — drop an mp3, wav, m4a, ogg, or flac.");
      return;
    }
    setUploading(file.name);
    try {
      const ep = await api.upload(showId, file, file.name.replace(/\.[a-z0-9]+$/i, ""));
      nav(`/ep/${ep.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
      setUploading(null);
    }
  };

  return (
    <div className="container">
      {show ? (
        <header className="show-head rise">
          <div className="show-head__swatch" aria-hidden>
            {show.palette.map((c) => (
              <span key={c} style={{ background: c }} />
            ))}
          </div>
          <h1 className="display show-head__name">{show.name}</h1>
          {show.tagline && <p className="show-head__tag">{show.tagline}</p>}
        </header>
      ) : (
        <div className="skeleton" style={{ height: 90, maxWidth: 480, marginBottom: "var(--s6)" }} />
      )}

      <section
        className={`dropzone ${dragOver ? "dropzone--over" : ""} ${uploading ? "dropzone--busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files[0];
          if (f) handleFile(f);
        }}
        onClick={() => !uploading && fileInput.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && fileInput.current?.click()}
        aria-label="Upload episode audio"
      >
        <input
          ref={fileInput}
          type="file"
          accept="audio/*"
          hidden
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        {uploading ? (
          <>
            <div className="dropzone__disc dropzone__disc--spin" aria-hidden />
            <p className="display dropzone__title">Uploading {uploading}…</p>
            <p className="dropzone__sub">Your episode is heading to the archive.</p>
          </>
        ) : (
          <>
            <div className="dropzone__disc" aria-hidden />
            <p className="display dropzone__title">Drop an episode</p>
            <p className="dropzone__sub">
              mp3 · wav · m4a · ogg · flac — the pipeline starts the moment it lands
            </p>
          </>
        )}
        {error && <p className="dropzone__error">{error}</p>}
      </section>

      <section className="eplist">
        <h2 className="caps eplist__head">Episodes</h2>
        {episodes === null ? (
          <div className="skeleton" style={{ height: 72 }} />
        ) : episodes.length === 0 ? (
          <p className="eplist__empty">Nothing here yet — your first episode is one drop away.</p>
        ) : (
          <ul className="eplist__items">
            {episodes.map((ep) => (
              <li key={ep.id}>
                <Link to={`/ep/${ep.id}`} className="ep-row">
                  <span className={`chip ${STATUS_CHIP[ep.status]}`}>{ep.status.replace("_", " ")}</span>
                  <span className="ep-row__title">{ep.title}</span>
                  <span className="ep-row__meta caps num">
                    {ep.word_count > 0 && `${ep.word_count} words · `}
                    {fmtBytes(ep.source.size_bytes)} · {relTime(ep.updated_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
