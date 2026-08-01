"""Durability tests — the queue, crash recovery, breakers, event log."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bside") + "/..")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BSIDE_DATA_DIR", str(tmp_path))
    from bside import config

    config.settings.cache_clear()
    from bside import db

    # reset thread-local connection so each test gets its own file
    db._local.__dict__.clear()
    db.init_db()
    yield db
    db._local.__dict__.clear()
    config.settings.cache_clear()


class TestJobQueue:
    def test_claim_marks_running_and_increments_attempts(self, fresh_db):
        jid = fresh_db.enqueue("ep1", "process")
        job = fresh_db.claim_ready_job()
        assert job is not None and job["id"] == jid
        assert job["attempts"] == 1
        assert fresh_db.claim_ready_job() is None  # nothing else ready

    def test_requeue_respects_backoff_time(self, fresh_db):
        fresh_db.enqueue("ep1", "process")
        job = fresh_db.claim_ready_job()
        fresh_db.requeue_job(job["id"], "boom", "2999-01-01T00:00:00+00:00")
        assert fresh_db.claim_ready_job() is None  # not due yet

    def test_crash_recovery_requeues_orphans(self, fresh_db):
        fresh_db.enqueue("ep1", "process")
        job = fresh_db.claim_ready_job()
        assert job is not None
        # simulate a crash: job left 'running'
        recovered = fresh_db.recover_orphans()
        assert recovered == 1
        again = fresh_db.claim_ready_job()
        assert again is not None and again["id"] == job["id"]
        assert "recovered after restart" in (again["error"] or "")

    def test_daily_budget_enforced(self, fresh_db):
        assert fresh_db.try_consume_daily_budget(2) is True
        assert fresh_db.try_consume_daily_budget(2) is True
        assert fresh_db.try_consume_daily_budget(2) is False


class TestEventLog:
    def test_append_and_replay_after_seq(self, fresh_db):
        fresh_db.append_event("ep1", "a", {})
        e2 = fresh_db.append_event("ep1", "b", {"x": 1})
        replay = fresh_db.events_after("ep1", 0)
        assert [e.type for e in replay] == ["a", "b"]
        assert fresh_db.events_after("ep1", e2.seq - 1)[0].type == "b"


class TestBreaker:
    def test_opens_after_threshold_and_half_opens(self, monkeypatch):
        from bside.providers import Breaker

        b = Breaker(name="t", threshold=2, cooldown_s=1000)
        assert b.allow()
        b.record_failure()
        assert b.allow()
        b.record_failure()
        assert not b.allow()
        assert b.state == "open"
        # simulate cooldown elapsed
        b.opened_at = -10_000.0
        assert b.allow()  # half-open probe
        b.record_success()
        assert b.state == "closed"


class TestErrorSanitizer:
    def test_presigned_query_strings_are_redacted(self):
        from bside.worker import sanitize_error

        msg = "failed https://s3.x.com/b/k.mp3?X-Amz-Signature=SECRET&X-Amz-Credential=KEY end"
        out = sanitize_error(msg)
        assert "SECRET" not in out and "?<redacted>" in out


class TestImageEvaluator:
    def _png(self, color) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color).save(buf, format="PNG")
        return buf.getvalue()

    def test_rejects_black_and_flat_accepts_textured(self):
        from bside.stages import evaluate_image_bytes

        ok, why = evaluate_image_bytes(self._png((0, 0, 0)))
        assert not ok and "near-black" in why
        ok, why = evaluate_image_bytes(self._png((120, 120, 120)))
        assert not ok and "flat" in why
        # textured image
        import io

        from PIL import Image

        im = Image.effect_noise((64, 64), 60).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        ok, why = evaluate_image_bytes(buf.getvalue())
        assert ok

    def test_rejects_undecodable(self):
        from bside.stages import evaluate_image_bytes

        ok, why = evaluate_image_bytes(b"not an image")
        assert not ok
