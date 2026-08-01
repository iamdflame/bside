"""The worker — durable, resumable, honest.

One background thread claims jobs from SQLite and walks the stage list.
Every stage checkpoint persists the Episode document to B2 *before*
advancing, so a kill -9 at any point resumes exactly where it stopped
(demonstrably — the judge flow does this live). Failures retry with
exponential backoff up to per-job budgets, then surface in the UI with
the real error and a retry button.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from genblaze_core.pipeline.cache import StepCache  # noqa: F401  (re-exported type)

from bside import db, storage
from bside.config import settings
from bside.events import emit
from bside.models import Episode, Show, StageName, StageStatus, utcnow
from bside.stages import STAGE_IMPL, StageContext, ValidatingStepCache

log = logging.getLogger("bside.worker")

_stop = threading.Event()
_threads: list[threading.Thread] = []
_active = threading.Semaphore(2)


_QS = re.compile(r"\?[^\s'\"]+")


def sanitize_error(msg: str) -> str:
    """Strip URL query strings (presigned credentials) from error text."""
    return _QS.sub("?<redacted>", msg)


def workdir_for(ep_id: str) -> Path:
    """Per-episode scratch under the system temp dir.

    Deliberately tmp-based: genblaze's transfer layer allowlists temp roots
    for file:// assets (SSRF/path-traversal defense), and losing scratch on
    restart is fine — every stage re-materializes what it needs from B2.
    """
    d = Path(tempfile.gettempdir()) / "bside-work" / ep_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backoff(attempt: int) -> str:
    delay = min(300, 5 * (2 ** (attempt - 1)))
    return (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()


def _load_show(show_id: str) -> Show:
    doc = db.get_show(show_id)
    if doc:
        return Show.model_validate(doc)
    show = storage.load_show(show_id)
    db.upsert_show(show.id, show.model_dump(mode="json"))
    return show


def _persist(ep: Episode) -> None:
    storage.save_episode(ep)
    db.upsert_episode(ep.id, ep.show_id, ep.status, ep.model_dump(mode="json"))


def process_episode(ep_id: str, *, from_stage: str | None = None) -> None:
    """Walk the stage list for one episode. Idempotent."""
    doc = db.get_episode(ep_id)
    ep = Episode.model_validate(doc) if doc else None
    if ep is None:
        # restore path: document plane is the source of truth
        for show_id in storage.list_show_ids():
            if ep_id in storage.list_episode_ids(show_id):
                ep = storage.load_episode(show_id, ep_id)
                break
    if ep is None:
        raise RuntimeError(f"episode {ep_id} not found in DB or B2")

    show = _load_show(ep.show_id)
    cache = ValidatingStepCache(settings().data_dir / "stepcache")
    ctx = StageContext(show=show, workdir=workdir_for(ep.id), cache=cache)

    ep.status = "processing"
    _persist(ep)
    emit(ep.id, "episode.processing", {"title": ep.title})

    started = False
    for record in ep.stages:
        stage = record.name
        if from_stage and not started:
            if stage.value != from_stage:
                continue
            started = True
            if record.status == StageStatus.DONE:
                record.status = StageStatus.PENDING  # explicit re-run request
        if record.status == StageStatus.DONE:
            continue

        record.status = StageStatus.RUNNING
        record.started_at = utcnow()
        record.attempts += 1
        record.error = None
        _persist(ep)
        emit(ep.id, "stage.started", {"stage": stage.value, "attempt": record.attempts})

        t0 = time.monotonic()
        try:
            STAGE_IMPL[stage](ep, ctx)
        except Exception as e:
            record.status = StageStatus.FAILED
            record.error = sanitize_error(f"{type(e).__name__}: {str(e)[:500]}")
            record.finished_at = utcnow()
            record.duration_s = round(time.monotonic() - t0, 2)
            ep.status = "failed"
            _persist(ep)
            emit(ep.id, "stage.failed", {
                "stage": stage.value, "error": record.error, "attempt": record.attempts,
            })
            log.error("stage %s failed for %s\n%s", stage, ep.id, traceback.format_exc())
            raise

        record.status = StageStatus.DONE
        record.finished_at = utcnow()
        record.duration_s = round(time.monotonic() - t0, 2)
        record.provider_notes = list(dict.fromkeys(record.provider_notes + ctx.notes))
        ctx.notes = []
        if stage != StageName.SEAL:
            ep.status = "processing"
        _persist(ep)
        emit(ep.id, "stage.done", {
            "stage": stage.value, "duration_s": record.duration_s,
            "notes": record.provider_notes[-3:],
        })

        # human-in-the-loop: pause before sealing until reviews arrive
        if stage == StageName.AUDIOGRAMS:
            pending = [a for a in ep.assets if a.review.value == "pending" and a.kind.value in (
                "episode_art", "quote_card", "audiogram")]
            if pending:
                ep.status = "in_review"
                _persist(ep)
                emit(ep.id, "episode.in_review", {"pending": len(pending)})
                return  # seal runs later, triggered by review completion

    emit(ep.id, "episode.done", {"status": ep.status, "release_key": ep.release_key})


def regenerate(ep_id: str, asset_id: str) -> None:
    """Re-run one rejected asset with reviewer feedback; lineage preserved."""
    from bside.stages import regenerate_asset

    doc = db.get_episode(ep_id)
    if not doc:
        raise RuntimeError(f"episode {ep_id} not found")
    ep = Episode.model_validate(doc)
    show = _load_show(ep.show_id)
    cache = ValidatingStepCache(settings().data_dir / "stepcache")
    ctx = StageContext(show=show, workdir=workdir_for(ep.id), cache=cache)

    emit(ep.id, "asset.regenerating", {"asset_id": asset_id})
    new = regenerate_asset(ep, ctx, asset_id)
    ep.status = "in_review"  # the new generation needs a human decision before sealing
    _persist(ep)
    emit(ep.id, "asset.regenerated", {
        "old_asset_id": asset_id, "new_asset_id": new.id,
        "generation": new.generation, "parent_run_id": new.parent_run_id,
    })


def _job_loop() -> None:
    while not _stop.is_set():
        job = db.claim_ready_job()
        if job is None:
            _stop.wait(1.0)
            continue
        with _active:
            jid, ep_id, kind = job["id"], job["episode_id"], job["kind"]
            payload = job["payload"]
            log.info("job %s %s ep=%s attempt=%s", jid, kind, ep_id, job["attempts"])
            try:
                if kind == "process":
                    if payload.get("regenerate"):
                        regenerate(ep_id, payload["regenerate"])
                    else:
                        process_episode(ep_id, from_stage=payload.get("from_stage"))
                elif kind == "seal":
                    process_episode(ep_id, from_stage=StageName.SEAL.value)
                else:
                    raise RuntimeError(f"unknown job kind {kind}")
                db.finish_job(jid, "done")
            except Exception as e:
                err = sanitize_error(f"{type(e).__name__}: {str(e)[:400]}")
                if job["attempts"] >= job["max_attempts"]:
                    db.finish_job(jid, "failed", error=err)
                    emit(ep_id, "job.failed", {"error": err, "attempts": job["attempts"]})
                else:
                    nxt = _backoff(job["attempts"])
                    db.requeue_job(jid, err, nxt)
                    emit(ep_id, "job.retry", {
                        "error": err, "attempt": job["attempts"], "next_attempt_at": nxt,
                    })


def start_worker(concurrency: int = 2) -> None:
    global _active
    _active = threading.Semaphore(max(1, concurrency))
    recovered = db.recover_orphans()
    if recovered:
        log.info("recovered %d orphaned running job(s) after restart", recovered)
    for i in range(max(1, concurrency)):
        t = threading.Thread(target=_job_loop, name=f"bside-worker-{i}", daemon=True)
        t.start()
        _threads.append(t)


def stop_worker() -> None:
    _stop.set()


def cleanup_workdirs(max_age_hours: int = 24) -> None:
    root = Path(tempfile.gettempdir()) / "bside-work"
    if not root.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
