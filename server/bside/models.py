"""Domain models — the vocabulary of a release kit.

The `Episode` document is the *system of record* and lives in B2 at
`shows/{show}/episodes/{ep}/episode.json`. SQLite is a disposable local
read-model/queue; the restore flow rebuilds it from the bucket alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StageName(StrEnum):
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    DIRECT = "direct"
    ART = "art"
    CARDS = "cards"
    AUDIOGRAMS = "audiograms"
    SEAL = "seal"


# Order matters: the worker walks this list; every stage is idempotent and
# resumable, so a crash mid-pipeline re-enters at the incomplete stage.
STAGE_ORDER: list[StageName] = [
    StageName.INGEST,
    StageName.TRANSCRIBE,
    StageName.DIRECT,
    StageName.ART,
    StageName.CARDS,
    StageName.AUDIOGRAMS,
    StageName.SEAL,
]


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class StageRecord(BaseModel):
    name: StageName
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    # Provenance surfaced to the evidence panel
    run_ids: list[str] = Field(default_factory=list)
    manifest_keys: list[str] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)  # e.g. "art: flux.1-dev → gemini fallback"
    cost_usd: float | None = None
    duration_s: float | None = None


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class Chapter(BaseModel):
    title: str
    start: float
    summary: str = ""


class Quote(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    text: str
    start: float
    end: float
    reason: str = ""  # why the model picked it — surfaced in UI


class Direction(BaseModel):
    """Everything the LLM derives from the transcript to direct the kit."""

    titles: list[str] = Field(default_factory=list)
    summary: str = ""
    show_notes_md: str = ""
    chapters: list[Chapter] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    art_brief: str = ""
    palette: list[str] = Field(default_factory=list)  # hex colors derived from mood


class KitAssetKind(StrEnum):
    SOURCE_AUDIO = "source_audio"
    TRANSCRIPT = "transcript"
    DIRECTION = "direction"
    EPISODE_ART = "episode_art"
    QUOTE_CARD = "quote_card"
    AUDIOGRAM = "audiogram"
    RELEASE_ZIP = "release_zip"


class KitAsset(BaseModel):
    id: str = Field(default_factory=lambda: new_id("a"))
    kind: KitAssetKind
    label: str = ""
    b2_key: str = ""
    sha256: str = ""
    size_bytes: int = 0
    media_type: str = ""
    # Genblaze lineage
    run_id: str | None = None
    parent_run_id: str | None = None
    manifest_key: str | None = None
    provider: str | None = None
    model: str | None = None
    quote_id: str | None = None  # for cards/audiograms
    review: ReviewState = ReviewState.PENDING
    review_feedback: str = ""
    generation: int = 1  # bumped on regeneration
    created_at: str = Field(default_factory=utcnow)


class SourceInfo(BaseModel):
    filename: str = ""
    media_type: str = ""
    size_bytes: int = 0
    duration_s: float | None = None
    sha256: str = ""
    b2_key: str = ""


class Episode(BaseModel):
    """System-of-record document. Persisted to B2 after every stage."""

    id: str = Field(default_factory=lambda: new_id("ep"))
    show_id: str
    title: str = "Untitled episode"
    status: str = "created"  # created | processing | in_review | sealed | failed
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    source: SourceInfo = Field(default_factory=SourceInfo)
    stages: list[StageRecord] = Field(default_factory=lambda: [StageRecord(name=n) for n in STAGE_ORDER])
    transcript_key: str = ""
    word_count: int = 0
    direction: Direction | None = None
    assets: list[KitAsset] = Field(default_factory=list)
    release_key: str = ""
    release_version: int = 0

    def stage(self, name: StageName) -> StageRecord:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(name)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def assets_of(self, kind: KitAssetKind) -> list[KitAsset]:
        return [a for a in self.assets if a.kind == kind]

    def asset(self, asset_id: str) -> KitAsset:
        for a in self.assets:
            if a.id == asset_id:
                return a
        raise KeyError(asset_id)


class Show(BaseModel):
    """Show canon — persistent style identity applied to every episode."""

    id: str = Field(default_factory=lambda: new_id("show"))
    name: str
    tagline: str = ""
    style_canon: str = (
        "Bold editorial illustration, grainy risograph texture, deep midnight-blue field with one "
        "electric accent color, strong geometric composition, no text, no lettering, no watermarks."
    )
    palette: list[str] = Field(default_factory=lambda: ["#0B0E1A", "#4C5FFF", "#F5F1E8"])
    created_at: str = Field(default_factory=utcnow)
    episode_ids: list[str] = Field(default_factory=list)


class EventRecord(BaseModel):
    """One SSE event — also persisted for replay/inspection."""

    seq: int = 0
    episode_id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=utcnow)
