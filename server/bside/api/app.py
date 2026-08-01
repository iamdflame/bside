"""FastAPI application — one service: API + SSE + static SPA.

No accounts, no sign-in walls (the #1 judge-killer in the audited field).
Abuse is bounded instead by rate limits, size caps, daily budgets, and a
global pipeline concurrency gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bside import db, events, keys, storage, worker
from bside.config import settings
from bside.models import (
    Episode,
    KitAssetKind,
    ReviewState,
    Show,
    SourceInfo,
    StageStatus,
)
from bside.providers import breaker_states

log = logging.getLogger("bside.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    events.bind_loop(asyncio.get_running_loop())
    worker.start_worker(concurrency=settings().max_concurrent_pipelines)
    worker.cleanup_workdirs()
    log.info("b-side up — providers: %s", settings().provider_status())
    yield
    worker.stop_worker()


app = FastAPI(
    title="B-Side", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan
)

ALLOWED_AUDIO = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# ---------- rate limiting (simple sliding window per client) ----------

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else request.client.host if request.client else "?")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method != "GET":
        ip = _client_ip(request)
        now = time.monotonic()
        limit = settings().rate_limit_per_minute
        with _hits_lock:
            q = _hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return JSONResponse({"error": "rate limited — try again in a minute"}, status_code=429)
            q.append(now)
    return await call_next(request)


# ---------- health & meta ----------


@app.get("/api/health")
def health() -> dict:
    s = settings()
    return {
        "ok": True,
        "b2": s.b2_configured,
        "providers": s.provider_status(),
        "breakers": breaker_states(),
        "models": {
            "stt": s.stt_model,
            "chat": s.chat_model,
            "image_primary": s.image_model_nvidia if s.nvidia_api_key else s.image_model_gemini,
            "image_fallback": s.image_model_gemini,
        },
    }


# ---------- shows ----------


class CreateShow(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tagline: str = Field(default="", max_length=140)
    style_canon: str | None = None
    palette: list[str] | None = None


@app.post("/api/shows")
def create_show(body: CreateShow) -> dict:
    show = Show(name=body.name.strip(), tagline=body.tagline.strip())
    if body.style_canon:
        show.style_canon = body.style_canon[:500]
    if body.palette:
        show.palette = [p for p in body.palette if re.fullmatch(r"#[0-9a-fA-F]{6}", p)][:3] or show.palette
    storage.save_show(show)
    db.upsert_show(show.id, show.model_dump(mode="json"))
    return show.model_dump(mode="json")


@app.get("/api/shows")
def list_shows() -> list[dict]:
    return db.list_shows()


@app.get("/api/shows/{show_id}")
def get_show(show_id: str) -> dict:
    doc = db.get_show(show_id)
    if not doc:
        raise HTTPException(404, "show not found")
    return doc


# ---------- episodes ----------


@app.post("/api/shows/{show_id}/episodes")
async def upload_episode(
    show_id: str, request: Request, file: UploadFile, title: str = Form(default="")
) -> dict:
    show_doc = db.get_show(show_id)
    if not show_doc:
        raise HTTPException(404, "show not found")
    media_type = (file.content_type or "").lower()
    if media_type not in ALLOWED_AUDIO:
        raise HTTPException(415, f"unsupported audio type {media_type!r} — use mp3/wav/m4a/ogg/flac")
    if not db.try_consume_daily_budget(settings().daily_episode_budget):
        raise HTTPException(429, "daily episode budget reached — try tomorrow")

    max_bytes = settings().max_upload_mb * 1024 * 1024
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(413, f"file too large (>{settings().max_upload_mb} MB)")
    if len(data) < 1024:
        raise HTTPException(400, "file too small to be an episode")

    safe_name = _SAFE_NAME.sub("-", file.filename or "episode-audio")[:80]
    ep = Episode(
        show_id=show_id,
        title=(title.strip() or Path(safe_name).stem.replace("-", " ").title())[:120],
        source=SourceInfo(filename=safe_name, media_type=media_type, size_bytes=len(data)),
    )
    wd = worker.workdir_for(ep.id)
    (wd / "upload").write_bytes(data)

    storage.save_episode(ep)
    db.upsert_episode(ep.id, show_id, ep.status, ep.model_dump(mode="json"))
    db.enqueue(ep.id, "process")
    events.emit(ep.id, "episode.created", {"title": ep.title, "size_bytes": len(data)})
    return ep.model_dump(mode="json")


@app.get("/api/episodes")
def list_episodes(show_id: str | None = None) -> list[dict]:
    return db.list_episodes(show_id)


@app.get("/api/episodes/{ep_id}")
def get_episode(ep_id: str) -> dict:
    doc = db.get_episode(ep_id)
    if not doc:
        raise HTTPException(404, "episode not found")
    return doc


class RetryBody(BaseModel):
    from_stage: str | None = None


@app.post("/api/episodes/{ep_id}/retry")
def retry_episode(ep_id: str, body: RetryBody) -> dict:
    doc = db.get_episode(ep_id)
    if not doc:
        raise HTTPException(404, "episode not found")
    ep = Episode.model_validate(doc)
    # reset failed stages so the walker re-enters them
    for s in ep.stages:
        if s.status == StageStatus.FAILED:
            s.status = StageStatus.PENDING
    ep.status = "processing"
    storage.save_episode(ep)
    db.upsert_episode(ep.id, ep.show_id, ep.status, ep.model_dump(mode="json"))
    db.enqueue(ep.id, "process", {"from_stage": body.from_stage} if body.from_stage else None)
    events.emit(ep.id, "episode.retry", {"from_stage": body.from_stage})
    return {"ok": True}


# ---------- review (human-in-the-loop) ----------


class ReviewBody(BaseModel):
    decision: ReviewState
    feedback: str = Field(default="", max_length=500)


@app.post("/api/episodes/{ep_id}/assets/{asset_id}/review")
def review_asset(ep_id: str, asset_id: str, body: ReviewBody) -> dict:
    doc = db.get_episode(ep_id)
    if not doc:
        raise HTTPException(404, "episode not found")
    ep = Episode.model_validate(doc)
    try:
        asset = ep.asset(asset_id)
    except KeyError as e:
        raise HTTPException(404, "asset not found") from e
    asset.review = body.decision
    asset.review_feedback = body.feedback.strip()
    storage.save_episode(ep)
    db.upsert_episode(ep.id, ep.show_id, ep.status, ep.model_dump(mode="json"))
    events.emit(ep.id, "asset.reviewed", {
        "asset_id": asset_id, "decision": body.decision.value, "kind": asset.kind.value,
    })

    if body.decision == ReviewState.REJECTED and asset.kind in (
        KitAssetKind.EPISODE_ART, KitAssetKind.QUOTE_CARD, KitAssetKind.AUDIOGRAM
    ):
        db.enqueue(ep.id, "process", {"regenerate": asset_id})

    reviewable = [a for a in ep.assets if a.kind in (
        KitAssetKind.EPISODE_ART, KitAssetKind.QUOTE_CARD, KitAssetKind.AUDIOGRAM)]
    if reviewable and all(a.review != ReviewState.PENDING for a in reviewable) and ep.status == "in_review":
        if all(a.review == ReviewState.APPROVED for a in reviewable if a.review != ReviewState.REJECTED):
            db.enqueue(ep.id, "seal")
            events.emit(ep.id, "episode.sealing", {})
    return {"ok": True}


# ---------- media & verification ----------


@app.get("/api/media")
def media(key: str) -> RedirectResponse:
    """Presigned delivery from the private bucket. Only app-plane keys."""
    if not (key.startswith(f"{keys.APP_PREFIX}/") or key.startswith(f"{keys.SINK_PREFIX}/")):
        raise HTTPException(400, "key outside served planes")
    if ".." in key:
        raise HTTPException(400, "bad key")
    try:
        url = storage.presigned_url(key, expires_in=900)
    except Exception as e:
        raise HTTPException(404, f"object unavailable: {type(e).__name__}") from e
    return RedirectResponse(url, status_code=307)


@app.get("/api/episodes/{ep_id}/verify/{asset_id}")
def verify_asset(ep_id: str, asset_id: str) -> dict:
    """Fetched-byte verification: download from B2 now, re-hash, compare."""
    doc = db.get_episode(ep_id)
    if not doc:
        raise HTTPException(404, "episode not found")
    ep = Episode.model_validate(doc)
    try:
        asset = ep.asset(asset_id)
    except KeyError as e:
        raise HTTPException(404, "asset not found") from e
    t0 = time.monotonic()
    result = storage.verify_object(asset.b2_key, asset.sha256)
    manifest_doc = None
    if asset.manifest_key:
        try:
            manifest_doc = storage.get_json(asset.manifest_key)
        except Exception:  # manifest fetch is best-effort for the panel
            manifest_doc = None
    return {
        "key": result.key,
        "expected_sha256": result.expected_sha256,
        "fetched_sha256": result.fetched_sha256,
        "size_bytes": result.size_bytes,
        "match": result.match,
        "fetch_ms": round((time.monotonic() - t0) * 1000),
        "manifest_key": asset.manifest_key,
        "manifest_canonical_hash": (manifest_doc or {}).get("canonical_hash"),
        "run_id": asset.run_id,
        "parent_run_id": asset.parent_run_id,
        "provider": asset.provider,
        "model": asset.model,
    }


@app.get("/api/episodes/{ep_id}/events")
async def sse_events(ep_id: str, request: Request, after: int = 0):
    """Server-sent events: replay from `after`, then live."""

    async def stream():
        q = events.subscribe(ep_id)
        try:
            last = after
            for rec in db.events_after(ep_id, last):
                last = rec.seq
                yield f"id: {rec.seq}\nevent: {rec.type}\ndata: {json.dumps(rec.model_dump())}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=15)
                    if rec.seq > last:
                        last = rec.seq
                        yield f"id: {rec.seq}\nevent: {rec.type}\ndata: {json.dumps(rec.model_dump())}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(ep_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


# ---------- restore (B2 is the system of record — prove it) ----------


@app.post("/api/restore")
def restore() -> dict:
    """Rebuild ALL local state from the bucket alone."""
    t0 = time.monotonic()
    shows, eps = 0, 0
    for show_id in storage.list_show_ids():
        try:
            show = storage.load_show(show_id)
        except Exception:
            continue
        db.upsert_show(show.id, show.model_dump(mode="json"))
        shows += 1
        for ep_id in storage.list_episode_ids(show_id):
            try:
                ep = storage.load_episode(show_id, ep_id)
            except Exception:
                continue
            db.upsert_episode(ep.id, ep.show_id, ep.status, ep.model_dump(mode="json"))
            eps += 1
    return {"shows": shows, "episodes": eps, "ms": round((time.monotonic() - t0) * 1000)}


# ---------- release ----------


@app.get("/api/episodes/{ep_id}/release")
def release_url(ep_id: str) -> dict:
    doc = db.get_episode(ep_id)
    if not doc:
        raise HTTPException(404, "episode not found")
    ep = Episode.model_validate(doc)
    if not ep.release_key:
        raise HTTPException(409, "kit not sealed yet")
    return {
        "key": ep.release_key,
        "version": ep.release_version,
        "url": storage.presigned_url(ep.release_key, expires_in=900),
    }


# ---------- judge mode ----------

FIXTURE_AUDIO = Path(__file__).resolve().parents[3] / "fixtures" / "demo-episode.mp3"
JUDGE_SHOW_NAME = "Signal Path"


def _judge_show_id() -> str | None:
    for s in db.list_shows():
        if s.get("name") == JUDGE_SHOW_NAME:
            return s["id"]
    return None


@app.get("/api/judge")
def judge_info() -> dict:
    """Everything a judge needs on one endpoint: demo state + live health."""
    s = settings()
    show_id = _judge_show_id()
    episodes = db.list_episodes(show_id) if show_id else []
    return {
        "show_id": show_id,
        "episodes": [
            {"id": e["id"], "title": e["title"], "status": e["status"], "updated_at": e["updated_at"]}
            for e in episodes[:10]
        ],
        "fixture_available": FIXTURE_AUDIO.exists(),
        "providers": s.provider_status(),
        "breakers": breaker_states(),
        "limits": {
            "daily_episodes": s.daily_episode_budget,
            "max_audio_minutes": s.max_audio_minutes,
        },
    }


@app.post("/api/judge/run")
def judge_run() -> dict:
    """One-click fresh pipeline run on the bundled fixture. Real providers,
    real B2 writes, bounded by the same budgets as everyone else."""
    if not FIXTURE_AUDIO.exists():
        raise HTTPException(503, "demo fixture not bundled in this deployment")
    show_id = _judge_show_id()
    if not show_id:
        show = Show(name=JUDGE_SHOW_NAME, tagline="How software actually gets shipped")
        storage.save_show(show)
        db.upsert_show(show.id, show.model_dump(mode="json"))
        show_id = show.id
    if not db.try_consume_daily_budget(settings().daily_episode_budget):
        raise HTTPException(429, "daily episode budget reached — try tomorrow")

    data = FIXTURE_AUDIO.read_bytes()
    ep = Episode(
        show_id=show_id,
        title=f"Judge run — {time.strftime('%H:%M:%S UTC', time.gmtime())}",
        source=SourceInfo(filename="demo-episode.mp3", media_type="audio/mpeg", size_bytes=len(data)),
    )
    (worker.workdir_for(ep.id) / "upload").write_bytes(data)
    storage.save_episode(ep)
    db.upsert_episode(ep.id, show_id, ep.status, ep.model_dump(mode="json"))
    db.enqueue(ep.id, "process")
    events.emit(ep.id, "episode.created", {"title": ep.title, "judge": True})
    return {"episode_id": ep.id, "show_id": show_id}


# ---------- static SPA ----------

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = WEB_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
