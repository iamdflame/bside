"""B2 key design — storage *as* orchestration.

Layout (one bucket, private):

    shows/{show_id}/show.json                              show canon document
    shows/{show_id}/episodes/{ep}/episode.json             system-of-record state
    shows/{show_id}/episodes/{ep}/source/{filename}        immutable ingested audio
    shows/{show_id}/episodes/{ep}/transcript/transcript.json
    shows/{show_id}/episodes/{ep}/kit/direction.json
    shows/{show_id}/episodes/{ep}/release/kit-v{n}.zip     sealed release bundles
    genblaze/runs/{show_id}/{date}/{run_id}/...            SDK-native manifests + assets
                                                           (tenant_id == show_id)
    scratch/...                                            lifecycle-expiring workspace

`genblaze/…` is owned by the Genblaze `ObjectStorageSink` (HIERARCHICAL key
strategy, tenant-partitioned by show). Everything else is the app's document
plane. The restore flow walks `shows/` and rebuilds all local state.
"""

from __future__ import annotations

APP_PREFIX = "shows"
SINK_PREFIX = "genblaze"
SCRATCH_PREFIX = "scratch"


def show_doc(show_id: str) -> str:
    return f"{APP_PREFIX}/{show_id}/show.json"


def episode_root(show_id: str, ep_id: str) -> str:
    return f"{APP_PREFIX}/{show_id}/episodes/{ep_id}"


def episode_doc(show_id: str, ep_id: str) -> str:
    return f"{episode_root(show_id, ep_id)}/episode.json"


def source_key(show_id: str, ep_id: str, filename: str) -> str:
    return f"{episode_root(show_id, ep_id)}/source/{filename}"


def transcript_key(show_id: str, ep_id: str) -> str:
    return f"{episode_root(show_id, ep_id)}/transcript/transcript.json"


def direction_key(show_id: str, ep_id: str) -> str:
    return f"{episode_root(show_id, ep_id)}/kit/direction.json"


def release_key(show_id: str, ep_id: str, version: int) -> str:
    return f"{episode_root(show_id, ep_id)}/release/kit-v{version}.zip"


def scratch_key(name: str) -> str:
    return f"{SCRATCH_PREFIX}/{name}"


def shows_list_prefix() -> str:
    return f"{APP_PREFIX}/"
