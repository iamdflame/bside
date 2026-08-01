"""Provider registry — real integrations, honest circuit breakers.

Every provider is real. There are no mock generation paths in the product.
When a provider is unconfigured or its breaker is open, the stage either
uses its declared fallback chain (visible in the UI) or fails honestly
with a retryable, explained error.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from bside.config import settings


@dataclass
class Breaker:
    """Per-provider circuit breaker: opens after N consecutive failures."""

    name: str
    threshold: int = 3
    cooldown_s: float = 120.0
    failures: int = 0
    opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                # half-open: allow one probe
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_at is None:
                return "closed"
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                return "half-open"
            return "open"


_breakers: dict[str, Breaker] = {}
_block = threading.Lock()


def breaker(name: str) -> Breaker:
    with _block:
        if name not in _breakers:
            _breakers[name] = Breaker(name=name)
        return _breakers[name]


def breaker_states() -> dict[str, str]:
    with _block:
        return {k: b.state for k, b in _breakers.items()}


# ---------- provider factories (lazy imports keep cold start fast) ----------


def stt_provider():
    from genblaze_assemblyai import AssemblyAIProvider

    return AssemblyAIProvider(api_key=settings().assemblyai_api_key or None)


def gemini_image_provider():
    from genblaze_google import GeminiImageProvider

    return GeminiImageProvider(api_key=settings().gemini_api_key or None)


def nvidia_image_provider():
    from genblaze_nvidia import NvidiaImageProvider

    return NvidiaImageProvider(api_key=settings().nvidia_api_key or None)


def image_plan() -> list[tuple[str, str]]:
    """Ordered (provider_name, model) attempts for image generation.

    NVIDIA FLUX is primary when configured (stronger art), Gemini-native
    image is the always-available fallback on a free key.
    """
    s = settings()
    plan: list[tuple[str, str]] = []
    if s.nvidia_api_key:
        plan.append(("nvidia", s.image_model_nvidia))
    if s.gemini_api_key:
        plan.append(("gemini", s.image_model_gemini))
    return plan


def gemini_chat(prompt: str, *, system: str | None = None, json_mode: bool = True) -> str:
    """LLM direction via the Genblaze google connector's chat() callable.

    `chat(model, ...)` retries 429s with the server's Retry-After hint —
    important on the free tier. Extra kwargs merge into generation_config,
    so response_mime_type gives strict JSON mode.
    """
    from genblaze_google import chat

    resp = chat(
        settings().chat_model,
        prompt=prompt,
        system=system,
        api_key=settings().gemini_api_key or None,
        retry_on_rate_limit=True,
        **({"response_mime_type": "application/json"} if json_mode else {}),
    )
    return resp.text
