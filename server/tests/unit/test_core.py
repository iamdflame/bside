"""Unit tests — pure logic, no network, no credentials."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bside") + "/..")

from bside import keys
from bside.design import resolve_palette
from bside.kit_providers import build_karaoke_ass
from bside.models import (
    STAGE_ORDER,
    Episode,
    KitAsset,
    KitAssetKind,
    StageName,
    WordTiming,
)
from bside.stages_direction import anchor_quote


class TestKeys:
    def test_hierarchy_is_stable(self):
        assert keys.episode_doc("s1", "e1") == "shows/s1/episodes/e1/episode.json"
        assert keys.source_key("s1", "e1", "a.mp3") == "shows/s1/episodes/e1/source/a.mp3"
        assert keys.release_key("s1", "e1", 3) == "shows/s1/episodes/e1/release/kit-v3.zip"
        assert keys.transcript_key("s1", "e1").startswith(keys.episode_root("s1", "e1"))

    def test_planes_are_disjoint(self):
        assert not keys.episode_root("s", "e").startswith(keys.SINK_PREFIX)
        assert not keys.scratch_key("x").startswith(keys.APP_PREFIX)


class TestQuoteAnchoring:
    WORDS = [
        WordTiming(word=w, start=i * 0.5, end=i * 0.5 + 0.4)
        for i, w in enumerate("the archive is not where work goes to die it is where".split())
    ]

    def test_exact_match_anchors_to_word_timings(self):
        hit = anchor_quote("archive is not where work", self.WORDS)
        assert hit is not None
        start, end, span = hit
        assert start == self.WORDS[1].start
        assert end == self.WORDS[5].end
        assert [w.word for w in span] == ["archive", "is", "not", "where", "work"]

    def test_case_and_punctuation_insensitive(self):
        hit = anchor_quote("The ARCHIVE, is not!", self.WORDS)
        assert hit is not None
        assert hit[0] == self.WORDS[0].start

    def test_no_match_returns_none(self):
        assert anchor_quote("completely absent words here", self.WORDS) is None


class TestKaraokeAss:
    def test_words_become_k_tags_with_centisecond_durations(self):
        words = [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.0},
        ]
        ass = build_karaoke_ass(words, palette=["#0B0E1A", "#4C5FFF", "#F5F1E8"])
        assert "[Script Info]" in ass and "Dialogue:" in ass
        # first word: 0.5s spoken + 0.1s gap folded in = 60cs
        assert "{\\k60}hello" in ass
        assert "{\\k40}world" in ass

    def test_line_breaks_on_long_gap(self):
        words = [
            {"word": "one", "start": 0.0, "end": 0.3},
            {"word": "two", "start": 3.0, "end": 3.3},  # 2.7s gap → new line
        ]
        ass = build_karaoke_ass(words, palette=[])
        assert ass.count("Dialogue:") == 2


class TestPalette:
    def test_orders_by_luminance(self):
        base, accent, paper = resolve_palette(["#F5F1E8", "#0B0E1A", "#4C5FFF"])
        assert base == (11, 14, 26)
        assert paper == (245, 241, 232)

    def test_all_dark_palette_synthesizes_paper(self):
        _, _, paper = resolve_palette(["#000000", "#111111", "#222222"])
        assert paper == (245, 241, 232)

    def test_garbage_hex_is_safe(self):
        base, accent, paper = resolve_palette(["not-a-color"])
        assert isinstance(base, tuple) and len(base) == 3


class TestEpisodeModel:
    def test_stage_walk_order_is_complete(self):
        ep = Episode(show_id="s1")
        assert [s.name for s in ep.stages] == STAGE_ORDER
        assert ep.stage(StageName.SEAL).status == "pending"

    def test_roundtrip_through_json(self):
        ep = Episode(show_id="s1", title="T")
        ep.assets.append(KitAsset(kind=KitAssetKind.EPISODE_ART, label="x", sha256="abc"))
        again = Episode.model_validate(ep.model_dump(mode="json"))
        assert again.assets[0].kind == KitAssetKind.EPISODE_ART
        assert again.asset(ep.assets[0].id).sha256 == "abc"
