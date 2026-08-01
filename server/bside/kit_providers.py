"""B-Side's custom Genblaze providers — the composition layer.

Two first-party `SyncProvider` subclasses extend the SDK exactly the way
its provider guide documents, so quote cards and audiograms are *pipeline
steps with manifests and lineage*, not out-of-band scripts:

- `QuoteCardProvider`  — model-made background + deterministic typography.
- `AudiogramProvider`  — ffmpeg composition: trimmed source audio, live
  waveform, and word-synced karaoke captions (ASS `\\k` tags) burned over
  a designed stage. The judge hears the creator's real voice inside a
  generated, captioned clip.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from genblaze_core.exceptions import ProviderError
from genblaze_core.models.asset import Asset, AudioMetadata, VideoMetadata
from genblaze_core.models.enums import Modality, ProviderErrorCode, StepType
from genblaze_core.models.step import Step
from genblaze_core.providers.base import ProviderCapabilities, SyncProvider
from genblaze_core.runnable.config import RunnableConfig

from bside import design

log = logging.getLogger("bside.providers")

FFMPEG_TIMEOUT = 300


def _local_path(url: str, workdir: Path, name: str) -> Path:
    """Materialize an asset URL (file:// or https://) as a local file."""
    if url.startswith("file://"):
        return Path(url[len("file://"):])
    if url.startswith(("http://", "https://")):
        dest = workdir / name
        with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as f:  # noqa: S310
            shutil.copyfileobj(resp, f)
        return dest
    p = Path(url)
    if p.exists():
        return p
    raise ProviderError(f"Cannot resolve input url: {url[:80]}", error_code=ProviderErrorCode.INVALID_INPUT)


def _file_asset(path: Path, media_type: str) -> Asset:
    data = path.read_bytes()
    return Asset(
        url=path.resolve().as_uri(),
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _run_ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1200:]
        raise ProviderError(f"ffmpeg failed: {tail}", error_code=ProviderErrorCode.PROVIDER_ERROR)


def ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],  # noqa: S607
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


class QuoteCardProvider(SyncProvider):
    """Deterministic quote-card composition as a manifest-carrying step."""

    name = "bside-quotecard"

    def __init__(self, output_dir: str | Path | None = None, **kw):
        super().__init__(**kw)
        self._output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.IMAGE],
            supported_inputs=["image"],
            accepts_chain_input=True,
            output_formats=["image/png"],
        )

    def generate(self, step: Step, config: RunnableConfig | None = None) -> Step:
        p = step.params or {}
        with tempfile.TemporaryDirectory(prefix="bside-card-") as td:
            workdir = Path(td)
            background: bytes | None = None
            for a in step.inputs or []:
                if a.media_type.startswith("image/"):
                    background = _local_path(a.url, workdir, "bg.png").read_bytes()
                    break
            png = design.render_quote_card(
                quote=p.get("quote", step.prompt or ""),
                attribution=p.get("attribution", ""),
                show_name=p.get("show_name", ""),
                episode_title=p.get("episode_title", ""),
                palette=list(p.get("palette", [])),
                background_png=background,
                timestamp_label=p.get("timestamp_label", ""),
            )
        out = self._output_dir / f"{step.step_id}.png"
        out.write_bytes(png)
        asset = _file_asset(out, "image/png")
        asset.width, asset.height = design.CARD_W, design.CARD_H
        step.assets.append(asset)
        step.step_type = StepType.EDIT
        return step


def _ass_time(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    r, g, b = design._hex_rgb(hex_color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def build_karaoke_ass(
    words: list[dict],
    *,
    palette: list[str],
    play_res: tuple[int, int] = (1080, 1350),
    max_line_chars: int = 26,
    gap_break_s: float = 0.9,
) -> str:
    """Word timings → ASS karaoke: words light up as they are spoken."""
    base, accent, paper = design.resolve_palette(palette)

    def hx(rgb: tuple[int, int, int]) -> str:
        return "#%02x%02x%02x" % rgb

    primary = _ass_color(hx(accent))          # after being spoken
    secondary = _ass_color(hx(paper), 0x50)   # before being spoken (dimmed)
    outline = _ass_color(hx(base), 0x00)

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {play_res[0]}\nPlayResY: {play_res[1]}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,Space Grotesk,58,{primary},{secondary},{outline},&H96000000,"
        "0,0,0,0,100,100,0.5,0,1,2,0,2,84,84,96,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # group words into caption lines
    lines: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    prev_end: float | None = None
    for w in words:
        token = str(w["word"]).strip()
        if not token:
            continue
        gap = (w["start"] - prev_end) if prev_end is not None else 0.0
        if cur and (cur_len + len(token) + 1 > max_line_chars or gap > gap_break_s):
            lines.append(cur)
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += len(token) + 1
        prev_end = w["end"]
    if cur:
        lines.append(cur)

    events = []
    for line in lines:
        start, end = line[0]["start"], line[-1]["end"] + 0.12
        parts = []
        for i, w in enumerate(line):
            dur_cs = max(1, round((w["end"] - w["start"]) * 100))
            # include inter-word silence in the preceding word's karaoke span
            if i + 1 < len(line):
                dur_cs += max(0, round((line[i + 1]["start"] - w["end"]) * 100))
            parts.append(f"{{\\k{dur_cs}}}{w['word'].strip()} ")
        text = "".join(parts).strip()
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Karaoke,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


class AudiogramProvider(SyncProvider):
    """Composes the audiogram MP4 — real voice, live waveform, word-synced captions."""

    name = "bside-audiogram"

    def __init__(self, output_dir: str | Path | None = None, **kw):
        super().__init__(**kw)
        self._output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.VIDEO],
            supported_inputs=["audio", "image"],
            accepts_chain_input=True,
            output_formats=["video/mp4"],
        )

    def generate(self, step: Step, config: RunnableConfig | None = None) -> Step:
        if shutil.which("ffmpeg") is None:
            raise ProviderError("ffmpeg not installed", error_code=ProviderErrorCode.PROVIDER_ERROR)
        p = step.params or {}
        start = float(p.get("start", 0.0))
        end = float(p.get("end", start + 30.0))
        duration = max(1.0, min(end - start, 60.0))
        words: list[dict] = list(p.get("words", []))
        palette: list[str] = list(p.get("palette", []))

        audio_in: Asset | None = None
        art_in: Asset | None = None
        for a in step.inputs or []:
            if a.media_type.startswith("audio/") and audio_in is None:
                audio_in = a
            elif a.media_type.startswith("image/") and art_in is None:
                art_in = a
        if audio_in is None:
            raise ProviderError("audiogram requires an audio input", error_code=ProviderErrorCode.INVALID_INPUT)

        out = self._output_dir / f"{step.step_id}.mp4"
        with tempfile.TemporaryDirectory(prefix="bside-agram-") as td:
            workdir = Path(td)
            audio_path = _local_path(audio_in.url, workdir, "source-audio")
            art_png = _local_path(art_in.url, workdir, "art.png").read_bytes() if art_in else None

            stage_png = design.render_audiogram_stage(
                show_name=p.get("show_name", ""),
                episode_title=p.get("episode_title", ""),
                quote=p.get("quote", ""),
                palette=palette,
                art_png=art_png,
            )
            stage_path = workdir / "stage.png"
            stage_path.write_bytes(stage_png)

            # word timings shifted to clip-local time
            local_words = [
                {"word": w["word"], "start": max(0.0, w["start"] - start), "end": max(0.0, w["end"] - start)}
                for w in words
                if w["end"] > start and w["start"] < end
            ]
            ass_path = workdir / "captions.ass"
            ass_path.write_text(build_karaoke_ass(local_words, palette=palette), encoding="utf-8")

            base, accent, paper = design.resolve_palette(palette)
            wave_color = "0x%02x%02x%02x" % accent

            filter_complex = (
                "[1:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
                "[a0]asplit=2[a1][a2];"
                f"[a1]showwaves=s=912x132:mode=cline:rate=30:colors={wave_color}[wv];"
                "[0:v][wv]overlay=x=84:y=926[v1];"
                f"[v1]ass={ass_path.as_posix()}:fontsdir={design.FONT_DIR.as_posix()}[vo]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-framerate", "30", "-i", str(stage_path),
                "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path),
                "-filter_complex", filter_complex,
                "-map", "[vo]", "-map", "[a2]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{duration:.3f}",
                "-movflags", "+faststart",
                str(out),
            ]
            _run_ffmpeg(cmd)

        asset = _file_asset(out, "video/mp4")
        asset.width, asset.height = design.CARD_W, design.CARD_H
        asset.duration = duration
        asset.video = VideoMetadata(codec="h264", frame_rate=30, has_audio=True, resolution="1080x1350")
        asset.audio = AudioMetadata(codec="aac", sample_rate=44100, channels=2)
        step.assets.append(asset)
        step.step_type = StepType.MIX
        return step
