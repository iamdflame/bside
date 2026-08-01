"""Create a real spoken mini-episode via the ElevenLabs Genblaze connector.

Produces fixtures/demo-episode.mp3 — real human-sounding speech used by the
E2E test and the judge-mode demo. ~1 minute, one narrator.
Run:  .venv/bin/python scripts/make_demo_audio.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from bside.b2env import load_dotenv  # noqa: E402

load_dotenv()

from genblaze_core import Modality, Pipeline  # noqa: E402
from genblaze_elevenlabs import ElevenLabsTTSProvider  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "fixtures"
OUT.mkdir(exist_ok=True)

SCRIPT = """
Welcome back to Signal Path, the show about how software actually gets shipped.
I'm your host, and today we're talking about the invisible half of creative work: everything
that happens after you hit stop on the recording.

Here's the thing nobody tells you when you start a podcast. The episode is the easy part.
The hard part is the release. You need a transcript for accessibility. Chapters for the feed.
Show notes that don't sound like a robot wrote them. Cover art. Quote cards for social.
And those little caption videos everyone shares now.

I used to spend three hours on this, every single week, across five different tools.
And the worst part? None of it was saved anywhere permanent. My archive was a folder
called final final version two on a laptop that nearly died last March.

So the principle I want to leave you with is this: your archive is not where work goes
to die. It is where your work goes to be believed. Store everything, prove everything,
and let the machines do the busywork so you can do the talking.

That's the show. See you next week on Signal Path.
""".strip()

result = (
    Pipeline("make-demo-audio")
    .step(
        ElevenLabsTTSProvider(output_dir=str(OUT)),
        model="eleven_multilingual_v2",
        prompt=SCRIPT,
        modality=Modality.AUDIO,
        voice_id="JBFqnCBsd6RMkjVDRZzb",
    )
    .run(timeout=300, raise_on_failure=True)
)
asset = result.run.steps[0].assets[0]
src = Path(asset.url.removeprefix("file://"))
dest = OUT / "demo-episode.mp3"
if src != dest:
    dest.write_bytes(src.read_bytes())
print("demo audio:", dest, f"{dest.stat().st_size/1024:.0f} KB sha256={asset.sha256[:16]}")
print("manifest verified:", result.manifest.verify())
