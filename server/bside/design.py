"""Deterministic design engine — typography the models can't garble.

Image models invent letterforms; we don't let them. Backgrounds come from
the art model; every glyph is set here with real fonts (Instrument Serif +
Space Grotesk, OFL, committed to the repo) so quote cards and audiogram
stages are typographically exact, palette-driven, and reproducible.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
SERIF = FONT_DIR / "InstrumentSerif-Regular.ttf"
SERIF_ITALIC = FONT_DIR / "InstrumentSerif-Italic.ttf"
GROTESK = FONT_DIR / "SpaceGrotesk.ttf"

CARD_W, CARD_H = 1080, 1350  # 4:5 — native to feeds


def _font(path: Path, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size=size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except OSError:
            pass
    return f


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        return (11, 14, 26)
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(rgb: tuple[int, int, int]) -> float:
    def chan(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def resolve_palette(palette: list[str]) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """(base, accent, paper) — base darkest, paper lightest, accent most saturated."""
    rgbs = [_hex_rgb(p) for p in palette[:3]] or [(11, 14, 26)]
    while len(rgbs) < 3:
        rgbs.append(rgbs[-1])
    by_lum = sorted(rgbs, key=_luminance)
    base, accent, paper = by_lum[0], by_lum[1], by_lum[2]
    if _luminance(paper) < 0.5:  # palette came in all-dark; synthesize paper
        paper = (245, 241, 232)
    return base, accent, paper


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = f"{cur} {w}".strip()
        if draw.textlength(probe, font=font) <= max_w:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_serif(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int,
               start: int = 104, floor: int = 44) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    size = start
    while size >= floor:
        font = _font(SERIF, size)
        lines = _wrap(draw, text, font, max_w)
        line_h = int(size * 1.08)
        if len(lines) * line_h <= max_h:
            return font, lines, line_h
        size -= 6
    font = _font(SERIF, floor)
    return font, _wrap(draw, text, font, max_w), int(floor * 1.08)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    resized = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (resized.width - w) // 2, (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    return m


def _grain(img: Image.Image, opacity: int = 10) -> Image.Image:
    import random

    rnd = random.Random(7)  # deterministic grain
    noise = Image.effect_noise(img.size, 28).convert("L")
    noise = noise.point(lambda p: min(255, int(p * (rnd.random() * 0 + 1))))
    overlay = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(img, overlay, opacity / 255)


def render_quote_card(
    *,
    quote: str,
    attribution: str,
    show_name: str,
    episode_title: str,
    palette: list[str],
    background_png: bytes | None,
    timestamp_label: str = "",
) -> bytes:
    """1080×1350 editorial quote card. Background from the art model; type set here."""
    base, accent, paper = resolve_palette(palette)
    card = Image.new("RGB", (CARD_W, CARD_H), base)

    if background_png:
        try:
            art = Image.open(io.BytesIO(background_png)).convert("RGB")
            art = _cover(art, CARD_W, CARD_H)
            art = ImageEnhance.Brightness(art).enhance(0.55)
            art = ImageEnhance.Color(art).enhance(0.82)
            art = art.filter(ImageFilter.GaussianBlur(2))
            card.paste(art, (0, 0))
            # readability scrim, bottom-weighted
            scrim = Image.new("L", (1, CARD_H))
            for y in range(CARD_H):
                scrim.putpixel((0, y), int(90 + 120 * (y / CARD_H)))
            scrim = scrim.resize((CARD_W, CARD_H))
            dark = Image.new("RGB", (CARD_W, CARD_H), base)
            card = Image.composite(dark, card, scrim.point(lambda p: p))
        except Exception:
            pass

    card = _grain(card, opacity=12)
    d = ImageDraw.Draw(card)
    margin = 96

    # top rule + show chip
    d.line([(margin, 118), (margin + 72, 118)], fill=accent, width=6)
    chip_font = _font(GROTESK, 30, weight=500)
    d.text((margin + 96, 100), show_name.upper(), font=chip_font, fill=paper)
    if timestamp_label:
        tw = d.textlength(timestamp_label, font=chip_font)
        d.text((CARD_W - margin - tw, 100), timestamp_label, font=chip_font, fill=accent)

    # oversized opening quote glyph
    glyph_font = _font(SERIF, 260)
    d.text((margin - 14, 168), "\u201C", font=glyph_font, fill=accent)

    # quote block
    quote_top, quote_h = 420, 610
    font, lines, line_h = _fit_serif(d, quote.strip(), CARD_W - margin * 2, quote_h)
    y = quote_top
    for line in lines:
        d.text((margin, y), line, font=font, fill=paper)
        y += line_h

    # attribution + footer
    attr_font = _font(GROTESK, 34, weight=500)
    d.text((margin, y + 40), f"— {attribution}" if attribution else "", font=attr_font, fill=accent)
    foot_font = _font(GROTESK, 28, weight=400)
    footer = episode_title[:60] + ("…" if len(episode_title) > 60 else "")
    d.text((margin, CARD_H - 96), footer, font=foot_font, fill=paper)
    d.line([(margin, CARD_H - 120), (CARD_W - margin, CARD_H - 120)], fill=(*accent, ), width=2)

    buf = io.BytesIO()
    card.save(buf, format="PNG")
    return buf.getvalue()


def render_audiogram_stage(
    *,
    show_name: str,
    episode_title: str,
    quote: str,
    palette: list[str],
    art_png: bytes | None,
) -> bytes:
    """The static stage the audiogram video is built on.

    Layout: framed art on top, kinetic zone (waveform + captions burn in
    via ffmpeg) below. 1080×1350.
    """
    base, accent, paper = resolve_palette(palette)
    stage = Image.new("RGB", (CARD_W, CARD_H), base)
    d = ImageDraw.Draw(stage)
    margin = 84

    if art_png:
        try:
            art = Image.open(io.BytesIO(art_png)).convert("RGB")
            frame_w, frame_h = CARD_W - margin * 2, 620
            art = _cover(art, frame_w, frame_h)
            mask = _rounded_mask((frame_w, frame_h), 28)
            stage.paste(art, (margin, 150), mask)
            d.rounded_rectangle(
                [margin, 150, margin + frame_w, 150 + frame_h], radius=28, outline=accent, width=3
            )
        except Exception:
            pass

    stage = _grain(stage, opacity=10)
    d = ImageDraw.Draw(stage)

    d.line([(margin, 96), (margin + 72, 96)], fill=accent, width=6)
    chip = _font(GROTESK, 30, weight=500)
    d.text((margin + 96, 78), show_name.upper(), font=chip, fill=paper)
    eq_label = "NOW PLAYING"
    d.text((CARD_W - margin - d.textlength(eq_label, font=chip), 78), eq_label, font=chip, fill=accent)

    title_font = _font(GROTESK, 40, weight=600)
    lines = _wrap(d, episode_title, title_font, CARD_W - margin * 2)[:2]
    ty = 810
    for line in lines:
        d.text((margin, ty), line, font=title_font, fill=paper)
        ty += 52

    buf = io.BytesIO()
    stage.save(buf, format="PNG")
    return buf.getvalue()


def render_fallback_art(*, title: str, palette: list[str], seed_text: str) -> bytes:
    """Deterministic generative-geometry cover used ONLY when every image
    provider is down — always labeled as fallback in provenance notes."""
    base, accent, paper = resolve_palette(palette)
    img = Image.new("RGB", (1080, 1080), base)
    d = ImageDraw.Draw(img)
    seed = sum(ord(c) for c in seed_text) or 7
    for i in range(14):
        r = 80 + ((seed * (i + 3)) % 420)
        cx = (seed * 37 * (i + 1)) % 1080
        cy = (seed * 53 * (i + 2)) % 1080
        color = accent if i % 3 else paper
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=3)
    img = img.filter(ImageFilter.GaussianBlur(1))
    img = _grain(img, 14)
    d = ImageDraw.Draw(img)
    d.line([(84, 96), (156, 96)], fill=accent, width=6)
    font = _font(SERIF, 88)
    y = 780
    for line in _wrap(d, title, font, 912)[:3]:
        d.text((84, y), line, font=font, fill=paper)
        y += 96
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
