"""Pipeline stages — every stage idempotent, resumable, and evidenced.

Contract per stage:
  run_<stage>(ep, ctx) -> None            mutates the Episode document
  - writes artifacts to B2 (sink or document plane) BEFORE returning
  - records run_ids / manifest_keys / provider notes on the StageRecord
  - safe to re-run after a crash: completed work is detected and skipped
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from genblaze_core import Modality, Pipeline
from genblaze_core.models.asset import Asset
from genblaze_core.pipeline.cache import StepCache

from bside import keys, storage
from bside.config import settings
from bside.events import emit
from bside.kit_providers import AudiogramProvider, QuoteCardProvider, ffprobe_duration
from bside.models import (
    Episode,
    KitAsset,
    KitAssetKind,
    ReviewState,
    Show,
    StageName,
    WordTiming,
)
from bside.providers import breaker, gemini_image_provider, image_plan, nvidia_image_provider
from bside.stages_direction import generate_direction

log = logging.getLogger("bside.stages")


@dataclass
class StageContext:
    show: Show
    workdir: Path
    cache: StepCache
    notes: list[str] = field(default_factory=list)

    def tmp(self, name: str) -> Path:
        return self.workdir / name


class ValidatingStepCache(StepCache):
    """StepCache that refuses hits whose asset bytes no longer exist.

    Cached steps reference file:// outputs in scratch space; after a restart
    or cleanup those files are gone and replaying the hit would poison the
    sink transfer. B2 (https) URLs are trusted; local ones must still exist.
    """

    def get(self, step, tenant_id=None):
        hit = super().get(step, tenant_id)
        if hit is None:
            return None
        for a in hit.assets or []:
            if a.url.startswith("file://") and not Path(a.url[len("file://"):]).exists():
                return None
        return hit


def _sink():
    return storage.sink()


def _emit_step(ep: Episode, stage: StageName, status: str, **data) -> None:
    emit(ep.id, f"stage.{status}", {"stage": stage.value, **data})


def _record_run(ep: Episode, stage: StageName, result) -> None:
    rec = ep.stage(stage)
    run = result.run
    if run.run_id not in rec.run_ids:
        rec.run_ids.append(run.run_id)
    if result.manifest and result.manifest.manifest_uri:
        key = storage.backend().key_from_url(result.manifest.manifest_uri) or result.manifest.manifest_uri
        if key not in rec.manifest_keys:
            rec.manifest_keys.append(key)
    cost = sum(s.cost_usd or 0 for s in run.steps)
    if cost:
        rec.cost_usd = (rec.cost_usd or 0) + cost


_MAGIC = [
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"GIF8", "image/gif"),
]


def _true_image_type(b2_key: str, claimed: str) -> str:
    """Providers sometimes mislabel bytes (FLUX returns JPEG as .png).

    A 16-byte ranged read from B2 settles it — manifests and kit records
    stay truthful about what the object actually is.
    """
    if not claimed.startswith("image/"):
        return claimed
    try:
        head = storage.backend().get_range(b2_key, offset=0, length=16)
    except Exception:
        return claimed
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    return claimed


def _kit_asset_from_step(ep: Episode, kind: KitAssetKind, result, *, label: str, quote_id: str | None = None,
                         provider: str | None = None, model: str | None = None) -> KitAsset:
    step = result.run.steps[-1]
    a = step.assets[0]
    b2_key = storage.backend().key_from_url(a.url) or ""
    manifest_key = None
    if result.manifest and result.manifest.manifest_uri:
        manifest_key = storage.backend().key_from_url(result.manifest.manifest_uri)
    ka = KitAsset(
        kind=kind,
        label=label,
        b2_key=b2_key,
        sha256=a.sha256 or "",
        size_bytes=a.size_bytes or 0,
        media_type=_true_image_type(b2_key, a.media_type) if b2_key else a.media_type,
        run_id=result.run.run_id,
        parent_run_id=result.run.parent_run_id,
        manifest_key=manifest_key,
        provider=provider or step.provider,
        model=model or step.model,
        quote_id=quote_id,
    )
    ep.assets.append(ka)
    return ka


# ---------------------------------------------------------------- ingest


def run_ingest(ep: Episode, ctx: StageContext) -> None:
    """Source audio → B2 via Pipeline.ingest → provenance manifest."""
    if ep.source.b2_key and any(a.kind == KitAssetKind.SOURCE_AUDIO for a in ep.assets):
        ctx.notes.append("ingest: already complete, skipped")
        return

    upload = ctx.tmp("upload")  # placed there by the API layer or restore
    if not upload.exists():
        # crash-recovery path: source may already be in B2
        if ep.source.b2_key and storage.exists(ep.source.b2_key):
            data = storage.get_bytes(ep.source.b2_key)
            upload.write_bytes(data)
        else:
            raise RuntimeError("no uploaded source found for ingest")

    data = upload.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    source_b2 = keys.source_key(ep.show_id, ep.id, ep.source.filename or "episode-audio")
    if not storage.exists(source_b2):
        storage.put_bytes(source_b2, data, ep.source.media_type or "audio/mpeg")
    ep.source.b2_key = source_b2
    ep.source.sha256 = sha
    ep.source.size_bytes = len(data)
    ep.source.duration_s = ffprobe_duration(upload)

    asset = Asset(
        url=upload.resolve().as_uri(),
        media_type=ep.source.media_type or "audio/mpeg",
        sha256=sha,
        size_bytes=len(data),
        duration=ep.source.duration_s,
    )
    result = Pipeline.ingest(
        [asset],
        source="creator-upload",
        source_metadata={
            "filename": ep.source.filename,
            "show_id": ep.show_id,
            "episode_id": ep.id,
            "b2_key": source_b2,
        },
        sink=_sink(),
        name=f"ingest-{ep.id}",
        tenant_id=ep.show_id,
    )
    _record_run(ep, StageName.INGEST, result)
    _kit_asset_from_step(ep, KitAssetKind.SOURCE_AUDIO, result, label="Source audio")
    ctx.notes.append(f"ingest: manifest verified={result.manifest.verify()}")


# ---------------------------------------------------------------- transcribe


def run_transcribe(ep: Episode, ctx: StageContext) -> None:
    if ep.transcript_key and storage.exists(ep.transcript_key):
        ctx.notes.append("transcribe: already complete, skipped")
        return
    from bside.providers import stt_provider

    br = breaker("assemblyai")
    if not br.allow():
        raise RuntimeError("assemblyai circuit open — retry later")

    audio_url = storage.presigned_url(ep.source.b2_key, expires_in=3600)
    stt = stt_provider()
    fallbacks = [m.strip() for m in settings().stt_fallback_models.split(",") if m.strip()]
    try:
        # Sinkless on purpose: the transcript is a TEXT asset (payload in
        # metadata["text"], sha256 over the text bytes) — persisted below via
        # Pipeline.ingest so the stored artifact carries its own manifest and
        # the bytes on B2 match the recorded hash exactly.
        result = (
            Pipeline(f"transcribe-{ep.id}", tenant_id=ep.show_id)
            .step(
                stt,
                model=settings().stt_model,
                fallback_models=fallbacks or None,
                modality=Modality.TEXT,
                audio_url=audio_url,
                prompt=f"transcribe {ep.source.filename}",
            )
            .run(timeout=1800, raise_on_failure=True)
        )
        br.record_success()
    except Exception:
        br.record_failure()
        raise

    step = result.run.steps[0]
    text_asset = step.assets[0]
    words_raw = (text_asset.audio.word_timings if text_asset.audio else None) or []
    words = [WordTiming(word=w.word, start=w.start, end=w.end) for w in words_raw]
    transcript_text = str(text_asset.metadata.get("text", ""))

    doc = {
        "episode_id": ep.id,
        "model": step.model,
        "text": transcript_text,
        "words": [w.model_dump() for w in words],
        "text_sha256": text_asset.sha256,
        "stt_run_id": result.run.run_id,
        "stt_manifest_canonical_hash": result.manifest.canonical_hash,
    }
    body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    local = ctx.tmp("transcript.json")
    local.write_bytes(body)

    ingest_result = Pipeline.ingest(
        [Asset(
            url=local.resolve().as_uri(),
            media_type="application/json",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
        )],
        source="bside-transcript",
        source_metadata={"episode_id": ep.id, "stt_run_id": result.run.run_id},
        sink=_sink(),
        name=f"transcript-{ep.id}",
        tenant_id=ep.show_id,
    )
    tkey = keys.transcript_key(ep.show_id, ep.id)
    storage.put_json(tkey, doc)  # document-plane copy at a stable, human-readable key
    ep.transcript_key = tkey
    ep.word_count = len(words)
    _record_run(ep, StageName.TRANSCRIBE, result)
    _record_run(ep, StageName.TRANSCRIBE, ingest_result)
    _kit_asset_from_step(ep, KitAssetKind.TRANSCRIPT, ingest_result, label="Transcript")
    ctx.notes.append(
        f"transcribe: {len(words)} words via {step.model}; "
        f"artifact manifest verified={ingest_result.manifest.verify()}"
    )


def load_words(ep: Episode) -> list[WordTiming]:
    doc = storage.get_json(ep.transcript_key)
    return [WordTiming.model_validate(w) for w in doc.get("words", [])]


# ---------------------------------------------------------------- direct


def run_direct(ep: Episode, ctx: StageContext) -> None:
    if ep.direction and ep.direction.quotes:
        ctx.notes.append("direct: already complete, skipped")
        return
    # provider breakers are managed inside gemini_chat (google → nvidia chain)
    words = load_words(ep)
    direction, notes = generate_direction(
        show_name=ctx.show.name, working_title=ep.title, words=words
    )
    ep.direction = direction
    if direction.titles:
        ep.title = direction.titles[0]
    if not direction.palette:
        direction.palette = ctx.show.palette
    storage.put_json(keys.direction_key(ep.show_id, ep.id), direction.model_dump(mode="json"))
    ctx.notes.extend(notes)
    from bside.providers import last_chat_provider

    ctx.notes.append(
        f"direct: {len(direction.chapters)} chapters, {len(direction.quotes)} anchored quotes "
        f"via {last_chat_provider() or settings().chat_model}"
    )


# ---------------------------------------------------------------- art


def _generate_image(ep: Episode, ctx: StageContext, *, prompt: str, label: str) -> tuple:
    """Image plan with breakers AND output evaluation.

    Every candidate's output is judged (blank/flat detection — FLUX can
    return an all-black frame on a filtered prompt). A failed evaluation
    is treated exactly like a provider error: noted honestly, next
    candidate tried. Returns (result, provider, model).
    """
    plan = image_plan()
    errors: list[str] = []
    for provider_name, model in plan:
        br = breaker(provider_name)
        if not br.allow():
            errors.append(f"{provider_name}: circuit open")
            continue
        for attempt in (1, 2):
            try:
                provider = nvidia_image_provider() if provider_name == "nvidia" else gemini_image_provider()
                nudge = "" if attempt == 1 else " Rich vivid color, high detail, luminous."
                # FLUX on NIM black-frames on an empty payload — pass explicit
                # generation params (verified against the raw API). Gemini's
                # generateContent path takes no diffusion params.
                params: dict = {}
                if provider_name == "nvidia":
                    params = {
                        "mode": "base",
                        "cfg_scale": 3.5,
                        "width": 1024,
                        "height": 1024,
                        "steps": 30,
                        "seed": (abs(hash(f"{ep.id}-{label}-{attempt}")) % 2**31),
                    }
                result = (
                    Pipeline(f"{label}-{ep.id}-{int(time.time())}", tenant_id=ep.show_id)
                    .cache(ctx.cache)
                    .step(provider, model=model, prompt=prompt + nudge,
                          modality=Modality.IMAGE, params=params)
                    .run(sink=_sink(), timeout=300, raise_on_failure=True)
                )
                br.record_success()
            except Exception as e:
                br.record_failure()
                errors.append(f"{provider_name}:{model} → {type(e).__name__}: {str(e)[:140]}")
                break  # provider-level failure → next provider

            asset = result.run.steps[-1].assets[0]
            b2_key = storage.backend().key_from_url(asset.url) or ""
            ok, verdict = evaluate_image_bytes(storage.get_bytes(b2_key)) if b2_key else (True, "unverified")
            if ok:
                if provider_name != plan[0][0] or attempt > 1:
                    ctx.notes.append(f"{label}: used {provider_name}:{model} (attempt {attempt})")
                ctx.notes.append(f"{label}: quality gate {verdict}")
                return result, provider_name, model
            errors.append(f"{provider_name}:{model} attempt {attempt} rejected by evaluator: {verdict}")
            ctx.notes.append(f"{label}: rejected {provider_name} output — {verdict}; retrying")
    raise RuntimeError(f"{label}: all image candidates failed → " + " | ".join(errors))


def evaluate_image_bytes(data: bytes) -> tuple[bool, str]:
    """Deterministic output evaluation: reject blank/flat frames."""
    import io as _io

    from PIL import Image as _Image

    try:
        im = _Image.open(_io.BytesIO(data)).convert("L")
    except Exception as e:
        return False, f"undecodable image ({type(e).__name__})"
    im.thumbnail((64, 64))
    px = list(im.getdata())
    mean = sum(px) / len(px)
    var = sum((p - mean) ** 2 for p in px) / len(px)
    if mean < 8:
        return False, f"near-black frame (mean={mean:.0f})"
    if mean > 247:
        return False, f"near-white frame (mean={mean:.0f})"
    if var < 40:
        return False, f"flat/monochrome frame (variance={var:.0f})"
    return True, f"passed (mean={mean:.0f}, variance={var:.0f})"


ART_PROMPT_SUFFIX = (
    " Abstract editorial cover art, album-cover quality, rich luminous color, strong composition,"
    " no text, no faces."
)


def _distill_brief(brief: str, limit: int = 220) -> str:
    """FLUX degrades (to black) on long prompts — keep the brief tight."""
    brief = " ".join(brief.split())
    if len(brief) <= limit:
        return brief
    cut = brief[:limit]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def run_art(ep: Episode, ctx: StageContext) -> None:
    if any(a.kind == KitAssetKind.EPISODE_ART for a in ep.assets):
        ctx.notes.append("art: already complete, skipped")
        return
    assert ep.direction is not None
    brief = _distill_brief(ep.direction.art_brief or f"Cover art for an episode titled {ep.title}")
    prompt = f"{brief}{ART_PROMPT_SUFFIX}"
    result, provider_name, model = _generate_image(ep, ctx, prompt=prompt, label="art")
    _record_run(ep, StageName.ART, result)
    _kit_asset_from_step(
        ep, KitAssetKind.EPISODE_ART, result, label="Episode art",
        provider=provider_name, model=model,
    )
    ctx.notes.append(f"art: {provider_name}:{model}, manifest verified={result.manifest.verify()}")


# ---------------------------------------------------------------- cards


def _art_bytes(ep: Episode) -> bytes | None:
    arts = [a for a in ep.assets if a.kind == KitAssetKind.EPISODE_ART]
    if not arts:
        return None
    return storage.get_bytes(arts[-1].b2_key)


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run_cards(ep: Episode, ctx: StageContext) -> None:
    assert ep.direction is not None
    done_quotes = {a.quote_id for a in ep.assets if a.kind == KitAssetKind.QUOTE_CARD}
    art_png = _art_bytes(ep)
    art_key = next((a.b2_key for a in ep.assets if a.kind == KitAssetKind.EPISODE_ART), None)
    palette = ep.direction.palette or ctx.show.palette

    for q in ep.direction.quotes:
        if q.id in done_quotes:
            continue
        external = []
        if art_png and art_key:
            local = ctx.tmp(f"art-{ep.id}.png")
            if not local.exists():
                local.write_bytes(art_png)
            external.append(Asset(
                url=local.resolve().as_uri(), media_type="image/png",
                sha256=hashlib.sha256(art_png).hexdigest(), size_bytes=len(art_png),
            ))
        result = (
            Pipeline(f"card-{q.id}", tenant_id=ep.show_id)
            .step(
                QuoteCardProvider(output_dir=ctx.workdir),
                model="bside-card-v1",
                prompt=q.text,
                modality=Modality.IMAGE,
                external_inputs=external or None,
                quote=q.text,
                attribution=ctx.show.name,
                show_name=ctx.show.name,
                episode_title=ep.title,
                palette=palette,
                timestamp_label=_fmt_ts(q.start),
            )
            .run(sink=_sink(), timeout=120, raise_on_failure=True)
        )
        _record_run(ep, StageName.CARDS, result)
        _kit_asset_from_step(
            ep, KitAssetKind.QUOTE_CARD, result,
            label=f"Quote card — “{q.text[:42]}…”", quote_id=q.id,
        )
        storage.save_episode(ep)  # checkpoint after each card
        _emit_step(ep, StageName.CARDS, "progress", quote_id=q.id)
    ctx.notes.append(f"cards: {len(ep.direction.quotes)} quote cards composed")


# ---------------------------------------------------------------- audiograms


def run_audiograms(ep: Episode, ctx: StageContext) -> None:
    assert ep.direction is not None
    done = {a.quote_id for a in ep.assets if a.kind == KitAssetKind.AUDIOGRAM}
    words = load_words(ep)
    palette = ep.direction.palette or ctx.show.palette

    src_local = ctx.tmp("source-audio")
    if not src_local.exists():
        src_local.write_bytes(storage.get_bytes(ep.source.b2_key))
    art_png = _art_bytes(ep)
    art_local = None
    if art_png:
        art_local = ctx.tmp("art-for-agram.png")
        if not art_local.exists():
            art_local.write_bytes(art_png)

    for q in ep.direction.quotes:
        if q.id in done:
            continue
        pad_start = max(0.0, q.start - 0.35)
        pad_end = q.end + 0.6
        q_words = [
            {"word": w.word, "start": w.start, "end": w.end}
            for w in words
            if w.end > pad_start and w.start < pad_end
        ]
        external = [Asset(
            url=src_local.resolve().as_uri(),
            media_type=ep.source.media_type or "audio/mpeg",
            sha256=ep.source.sha256, size_bytes=ep.source.size_bytes,
        )]
        if art_local and art_png:
            external.append(Asset(
                url=art_local.resolve().as_uri(), media_type="image/png",
                sha256=hashlib.sha256(art_png).hexdigest(), size_bytes=len(art_png),
            ))
        result = (
            Pipeline(f"audiogram-{q.id}", tenant_id=ep.show_id)
            .step(
                AudiogramProvider(output_dir=ctx.workdir),
                model="bside-audiogram-v1",
                prompt=q.text,
                modality=Modality.VIDEO,
                external_inputs=external,
                start=pad_start,
                end=pad_end,
                words=q_words,
                quote=q.text,
                show_name=ctx.show.name,
                episode_title=ep.title,
                palette=palette,
            )
            .run(sink=_sink(), timeout=600, raise_on_failure=True)
        )
        _record_run(ep, StageName.AUDIOGRAMS, result)
        _kit_asset_from_step(
            ep, KitAssetKind.AUDIOGRAM, result,
            label=f"Audiogram — “{q.text[:42]}…”", quote_id=q.id,
        )
        storage.save_episode(ep)
        _emit_step(ep, StageName.AUDIOGRAMS, "progress", quote_id=q.id)
    ctx.notes.append(f"audiograms: {len(ep.direction.quotes)} clips composed (word-synced captions)")


# ---------------------------------------------------------------- seal


def run_seal(ep: Episode, ctx: StageContext) -> None:
    """Assemble the release ZIP from approved (or all pending) assets + verify."""
    assert ep.direction is not None
    version = ep.release_version + 1
    zbuf = io.BytesIO()
    verified: list[dict] = []

    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("direction/show-notes.md", ep.direction.show_notes_md)
        z.writestr("direction/summary.txt", ep.direction.summary)
        z.writestr("direction/titles.txt", "\n".join(ep.direction.titles))
        chapters_txt = "\n".join(f"{_fmt_ts(c.start)} {c.title}" for c in ep.direction.chapters)
        z.writestr("direction/chapters.txt", chapters_txt)

        tdoc = storage.get_json(ep.transcript_key)
        z.writestr("transcript/transcript.txt", tdoc.get("text", ""))
        z.writestr("transcript/transcript.json", json.dumps(tdoc, ensure_ascii=False))

        for a in ep.assets:
            if a.kind in (KitAssetKind.SOURCE_AUDIO, KitAssetKind.TRANSCRIPT, KitAssetKind.RELEASE_ZIP):
                continue
            if a.review == ReviewState.REJECTED:
                continue
            data = storage.get_bytes(a.b2_key)
            fetched = hashlib.sha256(data).hexdigest()
            ok = fetched == a.sha256
            verified.append({"key": a.b2_key, "sha256": a.sha256, "fetched": fetched, "match": ok})
            if not ok:
                raise RuntimeError(f"seal: hash mismatch for {a.b2_key}")
            ext = a.media_type.split("/")[-1].replace("mpeg", "mp3")
            folder = {"episode_art": "art", "quote_card": "cards", "audiogram": "audiograms"}.get(
                a.kind.value, "assets"
            )
            z.writestr(f"{folder}/{a.id}.{ext}", data)

        z.writestr(
            "provenance/kit-manifest.json",
            json.dumps(
                {
                    "episode": ep.id,
                    "show": ep.show_id,
                    "sealed_at": time.time(),
                    "assets": verified,
                    "genblaze_manifests": sorted(
                        {k for s in ep.stages for k in s.manifest_keys}
                    ),
                },
                indent=2,
            ),
        )

    rkey = keys.release_key(ep.show_id, ep.id, version)
    storage.put_bytes(rkey, zbuf.getvalue(), "application/zip")
    ep.release_key = rkey
    ep.release_version = version
    ep.status = "sealed"
    ctx.notes.append(f"seal: kit v{version}, {len(verified)} assets fetched-byte verified")


STAGE_IMPL = {
    StageName.INGEST: run_ingest,
    StageName.TRANSCRIBE: run_transcribe,
    StageName.DIRECT: run_direct,
    StageName.ART: run_art,
    StageName.CARDS: run_cards,
    StageName.AUDIOGRAMS: run_audiograms,
    StageName.SEAL: run_seal,
}


# ---------------------------------------------------------------- regeneration


def regenerate_asset(ep: Episode, ctx: StageContext, asset_id: str) -> KitAsset:
    """Re-run one rejected asset with the reviewer's feedback, keeping lineage.

    The new run carries parent_run_id = the rejected asset's run, so the
    manifest chain records the human iteration loop (same mechanism as
    Pipeline.from_result — we hold only the run_id after a restart).
    """
    old = ep.asset(asset_id)
    feedback = old.review_feedback or "produce a noticeably different, stronger variation"
    palette = (ep.direction.palette if ep.direction else None) or ctx.show.palette

    if old.kind == KitAssetKind.EPISODE_ART:
        assert ep.direction is not None
        prompt = (
            f"{_distill_brief(ep.direction.art_brief, 160)}{ART_PROMPT_SUFFIX}"
            f" Revision: {_distill_brief(feedback, 140)}"
        )
        result, provider_name, model = _generate_image(ep, ctx, prompt=prompt, label="art-regen")
        result.run.parent_run_id = old.run_id
        new = _kit_asset_from_step(
            ep, KitAssetKind.EPISODE_ART, result, label=old.label,
            provider=provider_name, model=model,
        )
    elif old.kind == KitAssetKind.QUOTE_CARD:
        assert ep.direction is not None
        q = next((x for x in ep.direction.quotes if x.id == old.quote_id), None)
        if q is None:
            raise RuntimeError("quote no longer exists for card regeneration")
        art_png = _art_bytes(ep)
        external = []
        if art_png:
            local = ctx.tmp(f"art-{ep.id}.png")
            if not local.exists():
                local.write_bytes(art_png)
            external.append(Asset(
                url=local.resolve().as_uri(), media_type="image/png",
                sha256=hashlib.sha256(art_png).hexdigest(), size_bytes=len(art_png),
            ))
        pipe = Pipeline(f"card-regen-{q.id}", tenant_id=ep.show_id)
        pipe._parent_run_id = old.run_id  # lineage: same wiring as from_result
        result = (
            pipe.step(
                QuoteCardProvider(output_dir=ctx.workdir),
                model="bside-card-v1",
                prompt=q.text,
                modality=Modality.IMAGE,
                external_inputs=external or None,
                quote=q.text,
                attribution=ctx.show.name,
                show_name=ctx.show.name,
                episode_title=ep.title,
                palette=palette,
                timestamp_label=_fmt_ts(q.start),
                variation=feedback,  # cache-buster + recorded in manifest params
            )
            .run(sink=_sink(), timeout=120, raise_on_failure=True)
        )
        new = _kit_asset_from_step(
            ep, KitAssetKind.QUOTE_CARD, result, label=old.label, quote_id=q.id,
        )
    elif old.kind == KitAssetKind.AUDIOGRAM:
        assert ep.direction is not None
        q = next((x for x in ep.direction.quotes if x.id == old.quote_id), None)
        if q is None:
            raise RuntimeError("quote no longer exists for audiogram regeneration")
        words = load_words(ep)
        src_local = ctx.tmp("source-audio")
        if not src_local.exists():
            src_local.write_bytes(storage.get_bytes(ep.source.b2_key))
        art_png = _art_bytes(ep)
        external = [Asset(
            url=src_local.resolve().as_uri(),
            media_type=ep.source.media_type or "audio/mpeg",
            sha256=ep.source.sha256, size_bytes=ep.source.size_bytes,
        )]
        if art_png:
            art_local = ctx.tmp("art-for-agram.png")
            if not art_local.exists():
                art_local.write_bytes(art_png)
            external.append(Asset(
                url=art_local.resolve().as_uri(), media_type="image/png",
                sha256=hashlib.sha256(art_png).hexdigest(), size_bytes=len(art_png),
            ))
        pad_start, pad_end = max(0.0, q.start - 0.35), q.end + 0.6
        q_words = [{"word": w.word, "start": w.start, "end": w.end}
                   for w in words if w.end > pad_start and w.start < pad_end]
        pipe = Pipeline(f"audiogram-regen-{q.id}", tenant_id=ep.show_id)
        pipe._parent_run_id = old.run_id
        result = (
            pipe.step(
                AudiogramProvider(output_dir=ctx.workdir),
                model="bside-audiogram-v1",
                prompt=q.text,
                modality=Modality.VIDEO,
                external_inputs=external,
                start=pad_start, end=pad_end, words=q_words,
                quote=q.text, show_name=ctx.show.name, episode_title=ep.title,
                palette=palette,
                variation=feedback,
            )
            .run(sink=_sink(), timeout=600, raise_on_failure=True)
        )
        new = _kit_asset_from_step(
            ep, KitAssetKind.AUDIOGRAM, result, label=old.label, quote_id=q.id,
        )
    else:
        raise RuntimeError(f"asset kind {old.kind} is not regenerable")

    new.parent_run_id = old.run_id
    new.generation = old.generation + 1
    old.review = ReviewState.REJECTED  # rejected generation stays in history for the lineage panel
    return new
