"""B-Side runtime configuration.

Secrets come exclusively from the environment (never the repo).
Only B2_* and ASSEMBLYAI/GEMINI keys are strictly required for the full
pipeline; NVIDIA/ELEVENLABS unlock the primary art model and the trailer
voice, and the app degrades honestly (visible in the UI) without them.
"""

from __future__ import annotations

import os  # noqa: F401  (env is the config surface)
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # --- Backblaze B2 (system of record) ---
    b2_key_id: str = Field(default="", alias="B2_KEY_ID")
    b2_app_key: str = Field(default="", alias="B2_APP_KEY")
    b2_bucket: str = Field(default="", alias="B2_BUCKET")
    b2_region: str = Field(default="", alias="B2_REGION")

    # --- Providers ---
    assemblyai_api_key: str = Field(default="", alias="ASSEMBLYAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    nvidia_api_key: str = Field(default="", alias="NVIDIA_API_KEY")
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")

    # --- App ---
    data_dir: Path = Field(default=Path("./data"), alias="BSIDE_DATA_DIR")
    public_base_url: str = Field(default="http://localhost:8000", alias="BSIDE_PUBLIC_URL")
    # Judge/demo guardrails
    max_upload_mb: int = Field(default=200, alias="BSIDE_MAX_UPLOAD_MB")
    max_audio_minutes: int = Field(default=90, alias="BSIDE_MAX_AUDIO_MINUTES")
    max_concurrent_pipelines: int = Field(default=2, alias="BSIDE_MAX_CONCURRENT")
    daily_episode_budget: int = Field(default=40, alias="BSIDE_DAILY_EPISODES")
    rate_limit_per_minute: int = Field(default=30, alias="BSIDE_RATE_PER_MIN")

    # Models (overridable without code changes)
    stt_model: str = Field(default="universal", alias="BSIDE_STT_MODEL")
    chat_model: str = Field(default="gemini-2.5-flash", alias="BSIDE_CHAT_MODEL")
    image_model_nvidia: str = Field(
        default="black-forest-labs/flux.1-dev", alias="BSIDE_IMAGE_MODEL_NVIDIA"
    )
    image_model_gemini: str = Field(default="gemini-2.5-flash-image", alias="BSIDE_IMAGE_MODEL_GEMINI")
    tts_model: str = Field(default="eleven_multilingual_v2", alias="BSIDE_TTS_MODEL")
    tts_voice_id: str = Field(default="JBFqnCBsd6RMkjVDRZzb", alias="BSIDE_TTS_VOICE")

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "bside.db"

    @property
    def b2_configured(self) -> bool:
        return bool(self.b2_key_id and self.b2_app_key and self.b2_bucket)

    def provider_status(self) -> dict[str, bool]:
        """Which real integrations are live in this deployment (honest labels)."""
        return {
            "b2": self.b2_configured,
            "assemblyai": bool(self.assemblyai_api_key),
            "gemini": bool(self.gemini_api_key),
            "nvidia": bool(self.nvidia_api_key),
            "elevenlabs": bool(self.elevenlabs_api_key),
        }


@lru_cache
def settings() -> Settings:
    from bside.b2env import load_dotenv, normalize_b2_env

    load_dotenv()
    normalize_b2_env()
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
