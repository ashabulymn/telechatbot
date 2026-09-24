import json

import pytest

from app.config import get_settings
from app.providers.errors import ProviderConfigurationError
from app.providers.openai_responses import OpenAIResponsesProvider
from app.providers.anthropic import AnthropicMessagesProvider
from app.providers.gemini import GeminiProvider
from app.providers.registry import ProviderRegistry
from app.providers.registry_types import ProviderRuntime


def _reset_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    get_settings.cache_clear()


def test_registry_parses_attachment_modes(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps(
            {
                "responses": {
                    "protocol": "openai_responses",
                    "base_url": "https://example.com/v1",
                    "api_key": "key",
                    "default_model": "model",
                    "capabilities": ["text", "file", "transcription"],
                    "attachment_modes": {
                        "document": "native",
                        "audio": "transcribe",
                        "image": "fallback",
                        "video": "disabled",
                    },
                }
            }
        ),
    )

    registry = ProviderRegistry()
    profile = registry.profile("responses")

    assert profile is not None
    assert profile.attachment_modes == {
        "document": "native",
        "audio": "transcribe",
        "image": "fallback",
        "video": "disabled",
    }

    provider = registry.provider("responses")
    assert provider.attachment_mode(
        type("Item", (), {"kind": "audio"})()
    ) == "transcribe"


@pytest.mark.parametrize("mode", ["unknown", "", "native-ish"])
def test_registry_rejects_invalid_attachment_mode(monkeypatch, mode):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps(
            {
                "custom": {
                    "base_url": "https://example.com/v1",
                    "api_key": "key",
                    "default_model": "model",
                    "attachment_modes": {"document": mode},
                }
            }
        ),
    )

    with pytest.raises(ProviderConfigurationError):
        ProviderRegistry()


def test_responses_provider_attachment_mode_defaults_to_auto():
    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "file", "transcription"}),
        )
    )

    item = type("Item", (), {"kind": "document"})()
    assert provider.attachment_mode(item) == "auto"


def test_responses_provider_modes_control_native_and_transcription(tmp_path):
    from app.attachments import Attachment

    path = tmp_path / "voice.ogg"
    path.write_bytes(b"audio")

    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "file", "audio", "transcription"}),
            attachment_modes={"audio": "transcribe"},
        )
    )

    item = Attachment(
        kind="audio",
        file_id="audio-1",
        filename="voice.ogg",
        mime_type="audio/ogg",
        path=str(path),
    )

    assert provider.attachment_mode(item) == "transcribe"
    assert provider.supports_native_upload(item) is False
    assert provider.supports_transcription(item) is True


def test_responses_provider_disabled_mode_blocks_both_paths(tmp_path):
    from app.attachments import Attachment

    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")

    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "file", "transcription"}),
            attachment_modes={"document": "disabled"},
        )
    )

    item = Attachment(
        kind="document",
        file_id="doc-1",
        filename="note.txt",
        mime_type="text/plain",
        path=str(path),
    )

    assert provider.attachment_mode(item) == "disabled"
    assert provider.supports_native_upload(item) is False
    assert provider.supports_transcription(item) is False


def test_registry_provider_overrides_protocol_and_capabilities(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps(
            {
                "custom": {
                    "protocol": "openai_chat_completions",
                    "base_url": "https://example.com/v1",
                    "api_key": "key",
                    "default_model": "model",
                    "capabilities": ["text"],
                }
            }
        ),
    )
    registry = ProviderRegistry()
    provider = registry.provider(
        "custom",
        protocol_override="openai_responses",
        capabilities_override={"text", "file", "transcription"},
    )
    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.runtime.capabilities == frozenset({"text", "file", "transcription"})


def test_registry_supports_anthropic_messages(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps(
            {
                "anthropic": {
                    "protocol": "anthropic_messages",
                    "base_url": "https://api.anthropic.com",
                    "api_key": "key",
                    "default_model": "claude-test",
                    "capabilities": ["text", "vision"],
                }
            }
        ),
    )
    registry = ProviderRegistry()
    provider = registry.provider("anthropic")
    assert isinstance(provider, AnthropicMessagesProvider)
    assert provider._url() == "https://api.anthropic.com/v1/messages"


def test_registry_supports_gemini_generate_content(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps(
            {
                "gemini": {
                    "protocol": "gemini_generate_content",
                    "base_url": "https://generativelanguage.googleapis.com/v1beta",
                    "api_key": "key",
                    "default_model": "gemini-test",
                    "capabilities": ["text", "vision"],
                }
            }
        ),
    )
    registry = ProviderRegistry()
    provider = registry.provider("gemini")
    assert isinstance(provider, GeminiProvider)
    url, _, payload = provider._request([{"role": "user", "content": "hello"}])
    assert url.endswith("/models/gemini-test:generateContent")
    assert payload["contents"][0]["parts"][0]["text"] == "hello"


def test_registry_passes_model_capability_context(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setenv(
        "AI_PROVIDERS_JSON",
        json.dumps({
            "responses": {
                "protocol": "openai_responses",
                "base_url": "https://example.com/v1",
                "api_key": "key",
                "default_model": "model",
                "capabilities": ["text", "vision", "file"],
            }
        }),
    )
    provider = ProviderRegistry().provider(
        "responses",
        model_capabilities_override={"vision"},
        model_capabilities_known=True,
    )
    assert provider.runtime.model_capabilities == frozenset({"vision"})
    assert provider.runtime.model_capabilities_known is True


def test_responses_native_upload_respects_model_capabilities(tmp_path):
    from app.attachments import Attachment

    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    item = Attachment(
        kind="document",
        file_id="doc-1",
        filename="note.txt",
        mime_type="text/plain",
        path=str(path),
    )
    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "file"}),
            model_capabilities=frozenset({"vision"}),
            model_capabilities_known=True,
        )
    )
    assert provider.supports_native_upload(item) is False


def test_responses_prepare_skips_disabled_image(tmp_path):
    from app.attachments import Attachment

    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake-image")
    item = Attachment(
        kind="image",
        file_id="img-1",
        filename="photo.jpg",
        mime_type="image/jpeg",
        path=str(path),
    )
    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "vision", "file"}),
            attachment_modes={"image": "disabled"},
        )
    )

    async def run():
        return await provider.prepare_attachments([item])

    import asyncio
    mapping = asyncio.run(run())
    assert mapping.parts == []
    assert any("dinonaktifkan" in warning for warning in mapping.warnings)


def test_responses_prepare_skips_image_when_model_lacks_vision(tmp_path):
    from app.attachments import Attachment

    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake-image")
    item = Attachment(
        kind="image",
        file_id="img-1",
        filename="photo.jpg",
        mime_type="image/jpeg",
        path=str(path),
    )
    provider = OpenAIResponsesProvider(
        ProviderRuntime(
            name="x",
            base_url="https://example.com/v1",
            api_key="key",
            default_model="model",
            capabilities=frozenset({"text", "vision", "file"}),
            model_capabilities=frozenset({"text"}),
            model_capabilities_known=True,
        )
    )

    import asyncio
    mapping = asyncio.run(provider.prepare_attachments([item]))
    assert mapping.parts == []
    assert any("vision" in warning for warning in mapping.warnings)
