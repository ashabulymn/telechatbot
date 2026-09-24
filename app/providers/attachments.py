from dataclasses import dataclass
from typing import Any

from ..attachments import Attachment
from ..file_parser import FileParser


@dataclass(frozen=True)
class AttachmentMapping:
    parts: list[dict[str, Any]]
    note: str


class AttachmentAdapter:
    """Provider-neutral attachment mapper.

    The protocol is deliberately separate from Telegram. A future native
    provider adapter can implement file upload/transcription without changing
    bot handlers. The current OpenAI Chat Completions adapter uses image data
    URLs and bounded text extraction.
    """

    def __init__(self, parser: FileParser):
        self.parser = parser

    def map(self, attachments: list[Attachment], text: str = "") -> AttachmentMapping:
        parts: list[dict[str, Any]] = []
        notes: list[str] = []

        if text:
            parts.append({"type": "text", "text": text})

        # The parser budget is shared across all extracted attachments. Images
        # are provider-native payloads and do not consume the text budget.
        remaining = self.parser.max_chars
        for item in attachments:
            if item.is_image and item.data_url():
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": item.data_url()},
                })
                notes.append(
                    f"[Image: {item.filename}; MIME: {item.mime_type}; size: {item.size} bytes]"
                )
                continue

            extracted = (
                self.parser.extract(item.path, item.mime_type, item.filename)
                if item.path and remaining > 0 else None
            )
            if extracted:
                extracted = extracted[:remaining]
                remaining -= len(extracted)
                parts.append({
                    "type": "text",
                    "text": (
                        f"\n[File: {item.filename}; MIME: "
                        f"{item.mime_type or 'unknown'}]\n{extracted}"
                    ),
                })
            else:
                parts.append({
                    "type": "text",
                    "text": (
                        f"\n[Attachment: {item.filename or item.kind}; MIME: "
                        f"{item.mime_type or 'unknown'}; size: {item.size} bytes]"
                    ),
                })

            notes.append(
                f"[Attachment: {item.filename or item.kind}; MIME: "
                f"{item.mime_type or 'unknown'}; size: {item.size} bytes]"
            )

        if remaining <= 0 and len(attachments) > 1:
            parts.append({
                "type": "text",
                "text": "\n[Attachment text truncated: aggregate prompt limit reached.]",
            })

        return AttachmentMapping(parts=parts, note="\n".join(notes))
