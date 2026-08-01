"""Integration tests — the real B2 layer. Opt-in: requires B2_* env.

Run:  BSIDE_LIVE_B2=1 pytest server/tests/integration -q
Every object is created under scratch/tests/ and deleted afterwards.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bside") + "/..")

pytestmark = pytest.mark.skipif(
    not os.environ.get("BSIDE_LIVE_B2"),
    reason="live B2 integration is opt-in (set BSIDE_LIVE_B2=1)",
)


@pytest.fixture(scope="module")
def b2():
    from bside.b2env import load_dotenv, normalize_b2_env

    load_dotenv()
    normalize_b2_env()
    from bside import storage

    yield storage
    # cleanup everything the tests created
    for key in storage.list_keys("scratch/tests/"):
        storage.backend().delete(key)


def test_full_round_trip_with_fetched_byte_verification(b2):
    key = f"scratch/tests/rt-{int(time.time())}.txt"
    payload = f"integration {time.time()}".encode()
    expected = hashlib.sha256(payload).hexdigest()

    b2.put_bytes(key, payload, "text/plain")
    assert b2.exists(key)

    result = b2.verify_object(key, expected)
    assert result.match and result.size_bytes == len(payload)

    url = b2.presigned_url(key, expires_in=120)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        assert hashlib.sha256(resp.read()).hexdigest() == expected


def test_episode_document_round_trip_and_restore_listing(b2):
    from bside.models import Episode

    ep = Episode(show_id="test-show-int", title="Integration Episode")
    # write under the real document plane, then discover it from the bucket
    b2.save_episode(ep)
    try:
        loaded = b2.load_episode(ep.show_id, ep.id)
        assert loaded.title == "Integration Episode"
        assert ep.id in b2.list_episode_ids(ep.show_id)
        assert ep.show_id in b2.list_show_ids()
    finally:
        b2.backend().delete(f"shows/{ep.show_id}/episodes/{ep.id}/episode.json")


def test_verify_detects_tampering(b2):
    key = f"scratch/tests/tamper-{int(time.time())}.txt"
    b2.put_bytes(key, b"original bytes", "text/plain")
    original_hash = hashlib.sha256(b"original bytes").hexdigest()
    # overwrite the object — the recorded hash must now fail
    b2.put_bytes(key, b"tampered bytes!", "text/plain")
    result = b2.verify_object(key, original_hash)
    assert not result.match
