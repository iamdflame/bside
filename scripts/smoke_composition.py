"""Local smoke test: quote card + audiogram through a real Genblaze Pipeline.

No network, no credentials — proves the custom-provider composition layer
end-to-end: Pipeline → SyncProvider → ffmpeg → hash-verified manifest.
Run:  .venv/bin/python scripts/smoke_composition.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from genblaze_core import Modality, Pipeline  # noqa: E402
from genblaze_core.models.asset import Asset  # noqa: E402

from bside.kit_providers import AudiogramProvider, QuoteCardProvider, ffprobe_duration  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out-smoke"
OUT.mkdir(exist_ok=True)

PALETTE = ["#0B0E1A", "#4C5FFF", "#F5F1E8"]
QUOTE = "The archive is not where work goes to die - it is where it goes to be believed."

# 1) synthesize a spoken-ish test tone track (no TTS needed for smoke)
audio = OUT / "test-audio.wav"
subprocess.run(
    ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=196:duration=16",
     "-f", "lavfi", "-i", "sine=frequency=262:duration=16",
     "-filter_complex", "[0:a][1:a]amix=inputs=2,volume=0.4,atempo=1.0[a]",
     "-map", "[a]", "-ar", "44100", str(audio)],
    check=True, capture_output=True,
)
print("audio synthesized:", audio, ffprobe_duration(audio), "s")

# 2) quote card via Pipeline (no background: pure typographic card)
card_result = (
    Pipeline("smoke-card")
    .step(
        QuoteCardProvider(output_dir=OUT),
        model="bside-card-v1",
        prompt=QUOTE,
        modality=Modality.IMAGE,
        quote=QUOTE,
        attribution="Amara Osei",
        show_name="The B-Side Test",
        episode_title="Episode 12 - Proof over prose",
        palette=PALETTE,
        timestamp_label="12:41",
    )
    .run(timeout=60)
)
card_asset = card_result.run.steps[0].assets[0]
print("card:", card_asset.url, card_asset.sha256[:16], "verify:", card_result.manifest.verify())

# 3) audiogram via Pipeline with fabricated word timings
words = []
t = 1.0
for w in QUOTE.replace("-", " ").split():
    dur = 0.28 + 0.02 * len(w)
    words.append({"word": w, "start": round(t, 2), "end": round(t + dur, 2)})
    t += dur + 0.07

agram_result = (
    Pipeline("smoke-audiogram")
    .step(
        AudiogramProvider(output_dir=OUT),
        model="bside-audiogram-v1",
        prompt=QUOTE,
        modality=Modality.VIDEO,
        external_inputs=[
            Asset(url=audio.as_uri(), media_type="audio/wav"),
            Asset(url=card_asset.url, media_type="image/png"),
        ],
        start=0.0,
        end=min(t + 0.5, 15.5),
        words=words,
        quote=QUOTE,
        show_name="The B-Side Test",
        episode_title="Episode 12 - Proof over prose",
        palette=PALETTE,
    )
    .run(timeout=180)
)
ag_asset = agram_result.run.steps[0].assets[0]
print("audiogram:", ag_asset.url, ag_asset.sha256[:16], "dur:", ag_asset.duration,
      "verify:", agram_result.manifest.verify())
print("OK — composition layer proven end-to-end")
