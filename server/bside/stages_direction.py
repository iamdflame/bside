"""Direction — the editorial brain.

Gemini reads the transcript (with timestamps) and returns one structured
document: titles, summary, show notes, chapters, timestamp-anchored pull
quotes, and an art brief. Strict-JSON mode + schema validation + one
repair round; quote timestamps are then *re-anchored to the actual word
timings* so downstream cards and audiograms are sample-accurate.
"""

from __future__ import annotations

import json
import logging
import re

from bside.models import Chapter, Direction, Quote, WordTiming
from bside.providers import gemini_chat

log = logging.getLogger("bside.direction")

SYSTEM = """You are the senior producer for a podcast release. You read transcripts and produce
precise, tasteful release-kit direction. You never invent facts not present in the transcript.
Quotes must be VERBATIM spans from the transcript. Timestamps are seconds from episode start.
Respond with JSON only, matching the requested schema exactly."""

PROMPT_TEMPLATE = """SHOW: {show_name}
EPISODE WORKING TITLE: {working_title}

TRANSCRIPT (each line: [start_seconds] text):
{transcript_block}

Produce release-kit direction as JSON with EXACTLY this shape:
{{
  "titles": ["3 alternative episode titles, 40-70 chars, no clickbait"],
  "summary": "120-200 word episode summary in the show's voice",
  "show_notes_md": "markdown show notes: 1 intro paragraph, then '## Takeaways' with 4-6 bullets,\
then '## Chapters' with mm:ss timestamps",
  "chapters": [{{"title": "chapter title", "start": seconds_float, "summary": "one sentence"}}],
  "quotes": [{{"text": "VERBATIM quote 8-30 words, punchy, self-contained", "start": seconds_float,\
 "end": seconds_float, "reason": "why this quote earns a card"}}],
  "art_brief": "ONE sentence, max 180 characters: vivid, luminous, colorful abstract composition \
capturing the episode's core idea. Concrete imagery and mood. Never dark/dim scenes. NO text, NO faces.",
  "palette": ["#hex base (dark)", "#hex accent (vivid)", "#hex paper (light)"]
}}

Rules:
- 4-7 chapters, first at 0.0
- EXACTLY 3 quotes, chosen for shareability; start/end are the spoken span of that quote
- quote text must appear verbatim (case/punctuation may differ) in the transcript
- palette should fit the episode mood and keep strong dark/light contrast"""


def _transcript_block(words: list[WordTiming], max_chars: int = 24000) -> str:
    """Timestamped lines (~12s buckets) sized for the context window."""
    lines: list[str] = []
    bucket: list[str] = []
    bucket_start = 0.0
    for w in words:
        if not bucket:
            bucket_start = w.start
        bucket.append(w.word)
        if w.end - bucket_start >= 12.0:
            lines.append(f"[{bucket_start:.1f}] {' '.join(bucket)}")
            bucket = []
    if bucket:
        lines.append(f"[{bucket_start:.1f}] {' '.join(bucket)}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        head = block[: int(max_chars * 0.75)]
        tail = block[-int(max_chars * 0.2):]
        block = head + "\n[... transcript truncated for length ...]\n" + tail
    return block


_norm_re = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str) -> list[str]:
    return _norm_re.sub("", s.lower()).split()


def anchor_quote(quote_text: str, words: list[WordTiming]) -> tuple[float, float, list[WordTiming]] | None:
    """Find the quote's verbatim span in the word stream → exact timings."""
    target = _norm(quote_text)
    if not target:
        return None
    stream = [_norm(w.word)[0] if _norm(w.word) else "" for w in words]
    n, m = len(stream), len(target)
    for i in range(n - m + 1):
        if stream[i] == target[0] and stream[i : i + m] == target:
            span = words[i : i + m]
            return span[0].start, span[-1].end, span
    # fuzzy: allow 1-word slack at each end
    if m > 4:
        inner = target[1:-1]
        for i in range(n - len(inner) + 1):
            if stream[i : i + len(inner)] == inner:
                lo = max(0, i - 1)
                hi = min(n, i + len(inner) + 1)
                span = words[lo:hi]
                return span[0].start, span[-1].end, span
    return None


def _coerce(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def generate_direction(
    *, show_name: str, working_title: str, words: list[WordTiming]
) -> tuple[Direction, list[str]]:
    """Run the direction stage. Returns (direction, provider_notes)."""
    notes: list[str] = []
    prompt = PROMPT_TEMPLATE.format(
        show_name=show_name,
        working_title=working_title,
        transcript_block=_transcript_block(words),
    )
    raw = gemini_chat(prompt, system=SYSTEM, json_mode=True)
    try:
        doc = _coerce(raw)
    except json.JSONDecodeError:
        notes.append("direction: first response was not valid JSON; ran repair round")
        raw2 = gemini_chat(
            "The following was supposed to be valid JSON but is not. "
            f"Return the corrected JSON only.\n\n{raw}",
            system=SYSTEM,
            json_mode=True,
        )
        doc = _coerce(raw2)

    direction = Direction(
        titles=[str(t)[:90] for t in doc.get("titles", [])][:3],
        summary=str(doc.get("summary", ""))[:2000],
        show_notes_md=str(doc.get("show_notes_md", ""))[:8000],
        chapters=[
            Chapter(
                title=str(c.get("title", ""))[:80],
                start=float(c.get("start", 0.0)),
                summary=str(c.get("summary", ""))[:200],
            )
            for c in doc.get("chapters", [])[:8]
        ],
        art_brief=str(doc.get("art_brief", ""))[:1200],
        palette=[p for p in doc.get("palette", []) if re.fullmatch(r"#?[0-9a-fA-F]{6}", str(p))][:3],
    )
    direction.palette = [p if p.startswith("#") else f"#{p}" for p in direction.palette]

    # anchor quotes to real word timings — reject unanchorable ones
    anchored: list[Quote] = []
    for q in doc.get("quotes", [])[:5]:
        text = str(q.get("text", "")).strip()
        if not text:
            continue
        hit = anchor_quote(text, words)
        if hit:
            start, end, _span = hit
            anchored.append(Quote(text=text, start=start, end=end, reason=str(q.get("reason", ""))[:200]))
        else:
            lo = float(q.get("start", 0.0))
            hi = float(q.get("end", lo + 12.0))
            in_window = [w for w in words if w.start >= lo - 2 and w.end <= hi + 2]
            if in_window:
                anchored.append(
                    Quote(
                        text=" ".join(w.word for w in in_window)[:240],
                        start=in_window[0].start,
                        end=in_window[-1].end,
                        reason=(str(q.get("reason", "")) + " (re-derived from window)")[:200],
                    )
                )
                notes.append("direction: one quote re-derived from its timestamp window")
            else:
                notes.append("direction: dropped one unanchorable quote")
    direction.quotes = anchored[:3]

    if not direction.chapters:
        direction.chapters = [Chapter(title="Episode", start=0.0, summary="")]
        notes.append("direction: chapters missing; defaulted")
    return direction, notes
