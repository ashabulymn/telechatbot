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
