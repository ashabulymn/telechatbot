from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ..attachments import Attachment
from .attachments import AttachmentMapping, ProgressCallback, ProgressCallback


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

    async def prepare_attachments(
        self,
        attachments: list[Attachment],
        text: str = "",
        progress: ProgressCallback | None = None,
    ) -> AttachmentMapping:
        """Prepare attachments, optionally using provider native APIs."""
        if progress:
            for index, item in enumerate(attachments, 1):
                await progress(index, len(attachments), item, "preparing")
        return self.map_attachments(attachments, text)

    async def upload_attachment(self, attachment: Attachment) -> str | None:
        """Optional native upload hook.

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
