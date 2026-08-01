import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Show } from "../lib/api";
import { relTime } from "../lib/format";
import "./home.css";

export function HomePage() {
  const [shows, setShows] = useState<Show[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [tagline, setTagline] = useState("");
  const [error, setError] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    api.shows().then(setShows).catch(() => setShows([]));
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setError("");
    try {
      const show = await api.createShow({ name: name.trim(), tagline: tagline.trim() });
      nav(`/show/${show.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  };

  return (
    <div className="container">
      <section className="hero rise">
        <p className="caps hero__kicker">The release kit engine for podcasts &amp; recorded talks</p>
        <h1 className="display hero__title">
          You made the episode.
          <br />
          <em>B‑Side makes everything else.</em>
        </h1>
        <p className="hero__sub">
          Drop in your audio. Get back the transcript, chapters, show notes, episode art, quote
          cards, and word‑synced audiogram clips — every asset reviewed by you, provenance‑sealed,
          and archived to your show's permanent record on Backblaze B2.
        </p>
        <div className="hero__actions">
          <Link to="/judge" className="btn btn--signal">
            Watch it run live →
          </Link>
          <a href="#shows" className="btn btn--ghost">
            Start a show
          </a>
        </div>
        <div className="hero__steps caps">
          <span>ingest</span>
          <Arrow />
          <span>transcribe</span>
          <Arrow />
          <span>direct</span>
          <Arrow />
          <span>design</span>
          <Arrow />
          <span>compose</span>
          <Arrow />
          <span>review</span>
          <Arrow />
          <span>seal</span>
        </div>
      </section>

      <section id="shows" className="shows">
        <div className="shows__head">
          <h2 className="display shows__title">Shows</h2>
          <button className="btn" onClick={() => setCreating((v) => !v)}>
            {creating ? "Cancel" : "+ New show"}
          </button>
        </div>

        {creating && (
          <div className="card shows__create rise">
            <label className="caps" htmlFor="show-name">
              Show name
            </label>
            <input
              id="show-name"
              className="field"
              placeholder="Signal Path"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && create()}
            />
            <label className="caps" htmlFor="show-tag">
              Tagline
            </label>
            <input
              id="show-tag"
              className="field"
              placeholder="How software actually gets shipped"
              value={tagline}
              onChange={(e) => setTagline(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && create()}
            />
            {error && <p className="shows__error">{error}</p>}
            <button className="btn btn--signal" onClick={create} disabled={!name.trim()}>
              Create show
            </button>
          </div>
        )}

        {shows === null ? (
          <div className="shows__grid">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton show-card--skeleton" />
            ))}
          </div>
        ) : shows.length === 0 ? (
          <div className="card empty">
            <p className="display empty__title">No shows yet.</p>
            <p className="empty__sub">
              A show holds your style canon and your episode archive. Create one, drop in an
              episode, and B‑Side does the other half.
            </p>
          </div>
        ) : (
          <div className="shows__grid">
            {shows.map((s, i) => (
              <Link
                key={s.id}
                to={`/show/${s.id}`}
                className="card show-card rise"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="show-card__swatch" aria-hidden>
                  {s.palette.map((c) => (
                    <span key={c} style={{ background: c }} />
                  ))}
                </div>
                <h3 className="display show-card__name">{s.name}</h3>
                <p className="show-card__tag">{s.tagline || "—"}</p>
                <p className="caps num">{relTime(s.created_at)}</p>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Arrow() {
  return <span aria-hidden style={{ color: "var(--signal)" }}>→</span>;
}
