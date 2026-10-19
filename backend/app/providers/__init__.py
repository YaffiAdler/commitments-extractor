"""Extraction providers behind the ``ExtractionProvider`` interface."""
from __future__ import annotations

from app.config import ProviderName, Settings
from app.providers.base import ExtractionProvider
from app.providers.claude import ClaudeProvider
from app.providers.fake import FakeProvider
from app.providers.local_vlm import LocalVlmProvider


def create_provider(settings: Settings) -> ExtractionProvider:
    """Build the provider selected in ``settings.provider``."""
    if settings.provider is ProviderName.CLAUDE:
        return ClaudeProvider(model=settings.claude_model, timeout_s=settings.claude_timeout_s)
    if settings.provider is ProviderName.LOCAL_VLM:
        return LocalVlmProvider(base_url=settings.local_vlm_url)
    if settings.provider is ProviderName.FAKE:
        return FakeProvider()
    raise ValueError(f"unknown provider: {settings.provider}")
