from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class ProviderRuntime:
    name: str
    base_url: str
    api_key: str
    default_model: str
    extra_body: dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    attachment_modes: dict[str, str] = field(default_factory=dict)
