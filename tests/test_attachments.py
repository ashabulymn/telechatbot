from pathlib import Path

from app.attachments import Attachment
from app.file_parser import FileParser
from app.providers.attachments import AttachmentAdapter


def test_attachment_adapter_extracts_text(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("hello from attachment", encoding="utf-8")

    item = Attachment(
        kind="document",
        file_id="file-1",
        filename="note.txt",
        mime_type="text/plain",
        path=str(path),
    )
    mapped = AttachmentAdapter(FileParser(1000)).map([item], "summarize this")

    assert mapped.parts[0] == {"type": "text", "text": "summarize this"}
    assert "hello from attachment" in mapped.parts[1]["text"]
    assert "note.txt" in mapped.note


def test_attachment_adapter_applies_aggregate_text_budget(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("A" * 80, encoding="utf-8")
    second.write_text("B" * 80, encoding="utf-8")

    items = [
        Attachment(kind="document", file_id="1", filename="first.txt", mime_type="text/plain", path=str(first)),
        Attachment(kind="document", file_id="2", filename="second.txt", mime_type="text/plain", path=str(second)),
    ]
    mapped = AttachmentAdapter(FileParser(100)).map(items)

    extracted = "".join(
        p["text"] for p in mapped.parts if p.get("type") == "text"
    )
    assert extracted.count("A") == 80
    assert extracted.count("B") == 20
    assert "aggregate prompt limit reached" in extracted


def test_provider_native_upload_hook_defaults_to_unsupported(tmp_path):
    from app.providers.base import AIProvider

    class DummyProvider(AIProvider):
        async def chat(self, messages, model=None):
            raise NotImplementedError

    item = Attachment(
        kind="document",
        file_id="file-1",
        filename="note.txt",
        mime_type="text/plain",
        path=str(tmp_path / "note.txt"),
    )
    provider = DummyProvider()
    assert provider.supports_native_upload(item) is False


def test_audio_attachment_is_classified_as_audio():
    item = Attachment(
        kind="audio",
        file_id="voice-1",
        filename="voice.ogg",
        mime_type="audio/ogg",
    )
    assert item.kind == "audio"
    assert not item.is_image


def test_provider_runtime_capabilities_are_preserved():
    from app.providers.registry_types import ProviderRuntime

    runtime = ProviderRuntime(
        name="x",
        base_url="https://example.com/v1",
        api_key="key",
        default_model="model",
        capabilities=frozenset({"text", "file"}),
    )
    assert "file" in runtime.capabilities


@pytest.mark.asyncio
async def test_base_prepare_attachments_reports_progress(tmp_path):
    from app.providers.base import AIProvider

    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    item = Attachment(
        kind="document",
        file_id="1",
        filename="note.txt",
        mime_type="text/plain",
        path=str(path),
    )
    events = []

    class DummyProvider(AIProvider):
        async def chat(self, messages, model=None):
            raise NotImplementedError

    async def progress(index, total, attachment, state):
        events.append((index, total, attachment.filename, state))

    mapped = await DummyProvider().prepare_attachments([item], progress=progress)

    assert mapped.parts
    assert events == [(1, 1, "note.txt", "preparing")]


@pytest.mark.asyncio
async def test_responses_provider_native_upload_error_falls_back(tmp_path, monkeypatch):
    from app.providers.openai_responses import OpenAIResponsesProvider
    from app.providers.registry_types import ProviderRuntime
    from app.providers.errors import ProviderError

    path = tmp_path / "note.txt"
    path.write_text("fallback text", encoding="utf-8")
    item = Attachment(
        kind="document",
        file_id="1",
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
        )
    )

    async def fail_upload(attachment):
        raise ProviderError("simulated upload failure")

    monkeypatch.setattr(provider, "upload_attachment", fail_upload)
    events = []

    async def progress(index, total, attachment, state):
        events.append(state)

    mapped = await provider.prepare_attachments([item], progress=progress)

    assert "fallback text" in mapped.parts[-1]["text"]
    assert mapped.warnings
    assert "upload native gagal" in mapped.warnings[0]
    assert events == ["preparing", "uploading", "fallback", "ready"]
