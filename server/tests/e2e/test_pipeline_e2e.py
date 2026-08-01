"""End-to-end pipeline test through the composition layer.

Runs the API with a temp DB, exercises the custom Genblaze providers with
a synthesized audio fixture, and asserts the manifest/verification chain.
No paid providers, no B2 (unit-priced E2E: ingest→cards→audiogram logic
via the same Pipeline machinery production uses).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bside") + "/..")

from bside.kit_providers import AudiogramProvider, QuoteCardProvider  # noqa: E402
from genblaze_core import Modality, Pipeline  # noqa: E402
from genblaze_core.models.asset import Asset  # noqa: E402

FFMPEG = subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0  # noqa: S603,S607


@pytest.fixture(scope="module")
def audio_fixture(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("audio") / "tone.wav"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",  # noqa: S607
         "-ar", "44100", str(out)],
        check=True, capture_output=True,
    )
    return out


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_card_then_audiogram_pipeline_produces_verified_manifests(audio_fixture, tmp_path):
    palette = ["#0B0E1A", "#4C5FFF", "#F5F1E8"]
    quote = "the archive is where work goes to be believed"

    card = (
        Pipeline("e2e-card")
        .step(
            QuoteCardProvider(output_dir=tmp_path),
            model="bside-card-v1",
            prompt=quote,
            modality=Modality.IMAGE,
            quote=quote,
            attribution="E2E",
            show_name="E2E Show",
            episode_title="E2E Episode",
            palette=palette,
            timestamp_label="0:42",
        )
        .run(timeout=60, raise_on_failure=True)
    )
    card_asset = card.run.steps[0].assets[0]
    assert card.manifest.verify()
    assert card_asset.sha256 and card_asset.media_type == "image/png"
    assert card_asset.width == 1080 and card_asset.height == 1350

    words = [
        {"word": w, "start": 0.4 + i * 0.45, "end": 0.75 + i * 0.45}
        for i, w in enumerate(quote.split())
    ]
    agram = (
        Pipeline("e2e-audiogram")
        .step(
            AudiogramProvider(output_dir=tmp_path),
            model="bside-audiogram-v1",
            prompt=quote,
            modality=Modality.VIDEO,
            external_inputs=[
                Asset(url=audio_fixture.as_uri(), media_type="audio/wav", sha256="0" * 64),
                Asset(url=card_asset.url, media_type="image/png", sha256=card_asset.sha256),
            ],
            start=0.0,
            end=5.0,
            words=words,
            quote=quote,
            show_name="E2E Show",
            episode_title="E2E Episode",
            palette=palette,
        )
        .run(timeout=180, raise_on_failure=True)
    )
    v = agram.run.steps[0].assets[0]
    assert agram.manifest.verify()
    assert v.media_type == "video/mp4" and v.duration == 5.0
    # the produced file must be a real, probeable video
    probe = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "quiet", "-show_streams", "-print_format", "json",  # noqa: S607
         v.url.removeprefix("file://")],
        capture_output=True, text=True,
    )
    assert '"codec_type": "video"' in probe.stdout and '"codec_type": "audio"' in probe.stdout


def test_api_boots_and_serves_health(tmp_path, monkeypatch):
    monkeypatch.setenv("BSIDE_DATA_DIR", str(tmp_path))
    # never let this test touch a real bucket — strip credentials
    for var in ("B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET", "B2_BUCKET_ID", "B2_REGION"):
        monkeypatch.delenv(var, raising=False)
    # and stop settings() from re-reading a local .env
    monkeypatch.setattr("bside.b2env.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("bside.b2env.normalize_b2_env", lambda *a, **k: {})
    from bside import config

    config.settings.cache_clear()
    from bside import db
    from bside.api.app import app
    from fastapi.testclient import TestClient

    db._local.__dict__.clear()
    # raise_server_exceptions=False: without B2 creds the create handler 500s,
    # which is exactly the boundary we want the limiter test to survive
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and "providers" in body
        assert body["b2"] is False  # credentials stripped — flag must be honest
        # rate limiter: POST hammering returns 429 within the window
        codes = {client.post("/api/shows", json={"name": f"s{i}"}).status_code for i in range(45)}
        assert 429 in codes
    config.settings.cache_clear()
    db._local.__dict__.clear()
