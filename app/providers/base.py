from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ..attachments import Attachment
from .attachments import AttachmentMapping


@dataclass
class ProviderResponse:
    text: str
    raw: dict[str, Any] | None = None


class AIProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], model: str | None = None) -> ProviderResponse:
        raise NotImplementedError

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        response = await self.chat(messages, model)
        if response.text:
            yield response.text

    def map_attachments(
        self,
        attachments: list[Attachment],
        text: str = "",
    ) -> AttachmentMapping:
        """Fallback mapping for providers without native file APIs."""
        from .attachments import AttachmentAdapter
        from ..config import get_settings
        from ..file_parser import FileParser

        settings = get_settings()
        parser = FileParser(settings.attachment_max_prompt_chars)
        return AttachmentAdapter(parser).map(attachments, text)

    async def prepare_attachments(\n        self, attachments: list[Attachment], text: str = ""\n    ) -> AttachmentMapping:\n        """Prepare attachments, optionally using the provider native API."""\n        return self.map_attachments(attachments, text)\n\n    async def upload_attachment(self, attachment: Attachment) -> str | None:\n        """Optional native upload hook.

        Providers with a native file API can override this. Returning None is
        intentional: generic OpenAI-compatible providers must not assume that
        an arbitrary /files endpoint or file-id format exists.
        """
        return None

    def supports_native_upload(self, attachment: Attachment) -> bool:
        return False

    async def transcribe_attachment(self, attachment: Attachment) -> str | None:
        """Optional native speech-to-text hook for audio/voice attachments."""
        return None
