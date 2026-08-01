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


def _provider_tmp() -> str:
    """Provider outputs must land under a sink-allowed root (temp)."""
    import tempfile
    from pathlib import Path

    d = Path(tempfile.gettempdir()) / "bside-provider-out"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def stt_provider():
    from genblaze_assemblyai import AssemblyAIProvider

    return AssemblyAIProvider(api_key=settings().assemblyai_api_key or None)


def gemini_image_provider():
    from genblaze_google import GeminiImageProvider

    return GeminiImageProvider(api_key=settings().gemini_api_key or None, output_dir=_provider_tmp())


def nvidia_image_provider():
    from genblaze_nvidia import NvidiaImageProvider

    return NvidiaImageProvider(api_key=settings().nvidia_api_key or None, output_dir=_provider_tmp())


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
    """LLM direction via Genblaze chat() callables, with a real fallback chain.

    Primary: Gemini (`genblaze_google.chat`, retries 429s with Retry-After).
    Fallback: NVIDIA NIM chat (`genblaze_nvidia.chat`) when Gemini is
    unconfigured, exhausted, or its breaker is open. The caller's provider
    notes record which path produced the direction — honest labels, always.
    """
    s = settings()
    errors: list[str] = []

    if s.gemini_api_key and breaker("gemini-chat").allow():
        try:
            from genblaze_google import chat as g_chat

            resp = g_chat(
                s.chat_model,
                prompt=prompt,
                system=system,
                api_key=s.gemini_api_key,
                retry_on_rate_limit=True,
                **({"response_mime_type": "application/json"} if json_mode else {}),
            )
            breaker("gemini-chat").record_success()
            _last_chat_provider["name"] = f"google:{s.chat_model}"
            return resp.text
        except Exception as e:
            breaker("gemini-chat").record_failure()
            errors.append(f"gemini:{s.chat_model} → {type(e).__name__}: {str(e)[:140]}")

    if s.nvidia_api_key and breaker("nvidia-chat").allow():
        try:
            from genblaze_nvidia import chat as n_chat

            resp = n_chat(
                s.chat_model_nvidia,
                prompt=prompt,
                system=(system or "") + ("\nRespond with valid JSON only." if json_mode else ""),
                api_key=s.nvidia_api_key,
                temperature=0.4,
                max_tokens=4096,
                timeout=120.0,
            )
            breaker("nvidia-chat").record_success()
            _last_chat_provider["name"] = f"nvidia:{s.chat_model_nvidia}"
            return resp.text
        except Exception as e:
            breaker("nvidia-chat").record_failure()
            errors.append(f"nvidia:{s.chat_model_nvidia} → {type(e).__name__}: {str(e)[:140]}")

    raise RuntimeError("all chat providers failed → " + " | ".join(errors or ["none configured"]))


_last_chat_provider: dict[str, str] = {"name": ""}


def last_chat_provider() -> str:
    return _last_chat_provider["name"]
