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
        """Map Telegram attachments into this provider's message format."""
        from .attachments import AttachmentAdapter
        from ..file_parser import FileParser

        return AttachmentAdapter(FileParser()).map(attachments, text)
