"""Storage service — the B2 document plane + the Genblaze sink.

One private B2 bucket serves four planes:
  1. Document plane   (`shows/…`)    app state: show/episode JSON documents
  2. Media plane      (`shows/…`)    source audio, transcripts, release ZIPs
  3. Provenance plane (`genblaze/…`) SDK-owned manifests + generated assets
  4. Scratch plane    (`scratch/…`)  lifecycle-expiring workspace

All reads/writes go through `genblaze_s3.S3StorageBackend` — the same
storage backend the Genblaze sink uses, so one scoped application key and
one code path cover the whole product.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from genblaze_core import KeyStrategy, ObjectStorageSink
from genblaze_s3 import S3StorageBackend

from bside import keys
from bside.config import settings
from bside.models import Episode, Show

log = logging.getLogger("bside.storage")

_lock = threading.RLock()  # RLock: sink() calls backend() under the same lock
_backend: S3StorageBackend | None = None
_sink: ObjectStorageSink | None = None


def backend() -> S3StorageBackend:
    """Singleton S3-compatible backend bound to the B2 bucket."""
    global _backend
    with _lock:
        if _backend is None:
            s = settings()
            _backend = S3StorageBackend.for_backblaze(
                s.b2_bucket, key_id=s.b2_key_id or None, app_key=s.b2_app_key or None
            )
        return _backend


def sink() -> ObjectStorageSink:
    """Genblaze ObjectStorageSink — HIERARCHICAL, tenant-partitioned by show.

    Long-lived singleton: Pipeline.run() closes run-scoped sinks by default,
    so we use the SDK's documented opt-out (`_close_with_run = False`) to
    keep one sink (and its backend) alive across the app's many runs.
    """
    global _sink
    with _lock:
        if _sink is None:
            _sink = ObjectStorageSink(
                backend(),
                prefix=keys.SINK_PREFIX,
                key_strategy=KeyStrategy.HIERARCHICAL,
            )
            _sink._close_with_run = False  # SDK opt-out: caller owns lifecycle
        return _sink


# ---------- document plane ----------


def put_json(key: str, payload: dict[str, Any] | str) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
    data = body.encode("utf-8")
    backend().put(key, data, content_type="application/json")
    return hashlib.sha256(data).hexdigest()


def get_json(key: str) -> dict[str, Any]:
    return json.loads(backend().get(key).decode("utf-8"))


def put_bytes(key: str, data: bytes, content_type: str) -> str:
    backend().put(key, data, content_type=content_type)
    return hashlib.sha256(data).hexdigest()


def get_bytes(key: str) -> bytes:
    return backend().get(key)


def exists(key: str) -> bool:
    return backend().exists(key)


def presigned_url(key: str, expires_in: int = 3600) -> str:
    return backend().presigned_get(key, expires_in=expires_in).url


def list_keys(prefix: str) -> list[str]:
    """All keys under a prefix (paginated)."""
    out: list[str] = []
    token: str | None = None
    while True:
        page = backend().list(prefix=prefix, continuation_token=token)
        out.extend(e.key for e in page.entries)
        token = page.next_token
        if not token:
            return out


def save_episode(ep: Episode) -> None:
    ep.touch()
    put_json(keys.episode_doc(ep.show_id, ep.id), ep.model_dump(mode="json"))


def load_episode(show_id: str, ep_id: str) -> Episode:
    return Episode.model_validate(get_json(keys.episode_doc(show_id, ep_id)))


def save_show(show: Show) -> None:
    put_json(keys.show_doc(show.id), show.model_dump(mode="json"))


def load_show(show_id: str) -> Show:
    return Show.model_validate(get_json(keys.show_doc(show_id)))


def list_show_ids() -> list[str]:
    """Discover shows straight from the bucket — the restore entry point."""
    ids: set[str] = set()
    for key in list_keys(keys.shows_list_prefix()):
        parts = key.split("/")
        if len(parts) >= 2 and parts[0] == keys.APP_PREFIX and parts[1]:
            ids.add(parts[1])
    return sorted(ids)


def list_episode_ids(show_id: str) -> list[str]:
    ids: set[str] = set()
    prefix = f"{keys.APP_PREFIX}/{show_id}/episodes/"
    for key in list_keys(prefix):
        rest = key[len(prefix):]
        ep = rest.split("/", 1)[0]
        if ep:
            ids.add(ep)
    return sorted(ids)


# ---------- verification (fetched-byte evidence) ----------


@dataclass
class VerifyResult:
    key: str
    expected_sha256: str
    fetched_sha256: str
    size_bytes: int
    match: bool


def verify_object(key: str, expected_sha256: str) -> VerifyResult:
    """Download the object's actual bytes from B2 and re-hash them.

    This is the strongest storage evidence a judge can ask for: not a
    metadata claim, but a fresh byte-level round trip.
    """
    data = backend().get(key)
    fetched = hashlib.sha256(data).hexdigest()
    return VerifyResult(
        key=key,
        expected_sha256=expected_sha256,
        fetched_sha256=fetched,
        size_bytes=len(data),
        match=bool(expected_sha256) and fetched == expected_sha256,
    )
