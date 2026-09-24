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
