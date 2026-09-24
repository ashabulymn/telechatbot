from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
@dataclass
class ProviderResponse:
    text: str
    raw: dict[str,Any] | None = None
class AIProvider(ABC):
    @abstractmethod
    async def chat(self,messages:list[dict],model:str|None=None)->ProviderResponse: raise NotImplementedError
