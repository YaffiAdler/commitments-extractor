from app.config import ProviderName, Settings
from app.providers import create_provider
from app.providers.base import ExtractionProvider
from app.providers.claude import ClaudeProvider
from app.providers.fake import FakeProvider
from app.providers.gemini import GeminiProvider
from app.providers.local_vlm import LocalVlmProvider


def test_create_provider_by_setting(monkeypatch):
    assert isinstance(create_provider(Settings()), ClaudeProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.GEMINI.value)
    assert isinstance(create_provider(Settings()), GeminiProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.LOCAL_VLM.value)
    assert isinstance(create_provider(Settings()), LocalVlmProvider)
    monkeypatch.setenv("CE_PROVIDER", ProviderName.FAKE.value)
    assert isinstance(create_provider(Settings()), FakeProvider)


def test_all_providers_implement_protocol():
    for provider in (ClaudeProvider("m", 1.0), GeminiProvider("m", 1.0), LocalVlmProvider("http://x"), FakeProvider()):
        assert isinstance(provider, ExtractionProvider)
