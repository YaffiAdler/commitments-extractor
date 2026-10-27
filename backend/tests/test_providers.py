import numpy as np
import pytest

from app.config import ProviderName, Settings
from app.providers import create_provider
from app.providers.base import ExtractionProvider, ProviderError
from app.providers.claude import ClaudeProvider
from app.providers.fake import FakeProvider
from app.providers.gemini import GeminiProvider
from app.providers.local_vlm import LocalVlmProvider
from app.schemas import NameWording


def test_create_provider_by_setting(monkeypatch):
    assert isinstance(create_provider(Settings()), ClaudeProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.GEMINI.value)
    assert isinstance(create_provider(Settings()), GeminiProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.LOCAL_VLM.value)
    assert isinstance(create_provider(Settings()), LocalVlmProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.FAKE.value)
    assert isinstance(create_provider(Settings()), FakeProvider)


def test_all_providers_implement_protocol():
    for provider in (ClaudeProvider("m", 1.0), GeminiProvider("m", 1.0), LocalVlmProvider("http://x", "m", 1.0), FakeProvider()):
        assert isinstance(provider, ExtractionProvider)


def test_only_claude_and_the_fake_read_names():
    crop = np.zeros((10, 10, 3), np.uint8)
    for provider in (GeminiProvider("m", 1.0), LocalVlmProvider("http://x", "m", 1.0)):
        with pytest.raises(ProviderError, match="not supported"):
            provider.read_name(crop, "first_name", NameWording.STANDARD)
