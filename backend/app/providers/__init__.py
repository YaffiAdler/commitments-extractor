"""Extraction providers behind the ``ExtractionProvider`` interface."""
from __future__ import annotations

from app.config import ProviderName, Settings
from app.providers.base import ExtractionProvider
from app.providers.claude import ClaudeProvider
from app.providers.fake import FakeProvider
from app.providers.gemini import GeminiProvider
from app.providers.local_vlm import LocalVlmProvider
from app.providers.prompts import Prompts


def create_provider(settings: Settings) -> ExtractionProvider:
    """Build the provider selected in ``settings.provider``."""
    if settings.provider is ProviderName.FAKE:
        return FakeProvider()
    prompts = Prompts.load(settings.prompts_dir, settings.prompt_version)
    if settings.provider is ProviderName.CLAUDE:
        return ClaudeProvider(model=settings.claude_model, timeout_s=settings.claude_timeout_s, prompts=prompts,
                              debug=settings.debug_provider)
    if settings.provider is ProviderName.GEMINI:
        return GeminiProvider(model=settings.gemini_model, timeout_s=settings.gemini_timeout_s, prompts=prompts,
                              debug=settings.debug_provider)
    if settings.provider is ProviderName.LOCAL_VLM:
        return LocalVlmProvider(base_url=settings.local_vlm_url, model=settings.local_vlm_model,
                                timeout_s=settings.local_vlm_timeout_s, prompts=prompts)
    raise ValueError(f"unknown provider: {settings.provider}")
