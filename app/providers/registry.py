import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .base import AIProvider
from .openai_compatible import OpenAICompatibleProvider
from .openai_responses import OpenAIResponsesProvider
from .anthropic import AnthropicMessagesProvider
from .gemini import GeminiProvider
from .registry_types import ProviderRuntime
from .errors import ProviderConfigurationError
from ..config import get_settings


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    protocol: str = "openai_chat_completions"
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset({"text"})
    attachment_modes: dict[str, str] = field(default_factory=dict)


class ProviderRegistry:
    SUPPORTED_PROTOCOLS = frozenset({"openai_chat_completions", "openai_responses", "anthropic_messages", "gemini_generate_content"})
    CAPABILITIES = frozenset({"text", "vision", "pdf", "document", "audio", "video", "file", "file_cleanup", "transcription"})
    ATTACHMENT_MODES = frozenset({"auto", "native", "transcribe", "fallback", "disabled"})

    def __init__(self):
        self.settings = get_settings()
        self._profiles = self._load_profiles()

    def _load_profiles(self) -> dict[str, ProviderProfile]:
        raw = self.settings.ai_providers_json.strip()
        if not raw:
            return {"default": ProviderProfile(
                name="default", base_url=self.settings.ai_base_url,
                api_key=self.settings.ai_api_key, default_model=self.settings.ai_model,
                extra_body=self._extra_body(), capabilities=frozenset({"text", "vision"}), attachment_modes={}
            )}

        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError as exc:
            raise ProviderConfigurationError("AI_PROVIDERS_JSON bukan JSON object yang valid.") from exc

        profiles = {}
        for name, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            protocol = str(cfg.get("protocol", "openai_chat_completions")).strip()
            if protocol not in self.SUPPORTED_PROTOCOLS:
                raise ProviderConfigurationError(
                    f"Provider '{name}' memakai protocol '{protocol}' yang belum didukung."
                )
            raw_capabilities = cfg.get("capabilities", ["text"])
            if not isinstance(raw_capabilities, (list, tuple, set)):
                raise ProviderConfigurationError(f"Capabilities provider '{name}' harus berupa array.")
            capabilities = frozenset(str(x).strip().lower() for x in raw_capabilities if str(x).strip())
            unknown = capabilities - self.CAPABILITIES
            if unknown:
                raise ProviderConfigurationError(
                    f"Capability provider '{name}' tidak dikenal: {', '.join(sorted(unknown))}."
                )
            raw_modes = cfg.get("attachment_modes", {})
            if not isinstance(raw_modes, dict):
                raise ProviderConfigurationError(f"attachment_modes provider '{name}' harus berupa object.")
            attachment_modes = {}
            for kind, mode in raw_modes.items():
                kind = str(kind).strip().lower()
                mode = str(mode).strip().lower()
                if not kind or mode not in self.ATTACHMENT_MODES:
                    raise ProviderConfigurationError(
                        f"attachment_modes provider '{name}' tidak valid untuk '{kind}'."
                    )
                attachment_modes[kind] = mode
            profiles[name] = ProviderProfile(
                name=name, protocol=protocol,
                base_url=str(cfg.get("base_url", "")).strip(),
                api_key=self._resolve_secret(str(cfg.get("api_key", "")).strip()),
                default_model=str(cfg.get("default_model", "")).strip(),
                extra_body=cfg.get("extra_body") if isinstance(cfg.get("extra_body"), dict) else {},
                capabilities=capabilities,
                attachment_modes=attachment_modes,
            )
        if not profiles:
            raise ProviderConfigurationError("AI_PROVIDERS_JSON tidak memiliki provider yang valid.")
        return profiles

    def _resolve_secret(self, value):
        if value.startswith("$") and re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", value):
            return os.getenv(value[1:], "")
        return value

    def _extra_body(self):
        if not self.settings.ai_extra_body_json:
            return {}
        try:
            value = json.loads(self.settings.ai_extra_body_json)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def names(self):
        return list(self._profiles.keys())

    def get(self, name):
        return self._profiles.get(name)

    def provider(self, name=None, base_url_override=None, api_key_override=None, protocol_override=None, capabilities_override=None, model_capabilities_override=None, model_capabilities_known=False) -> AIProvider:
        selected = name or self.settings.ai_default_provider
        profile = self._profiles.get(selected)
        if not profile:
            raise ProviderConfigurationError(f"Provider '{selected}' tidak ditemukan.")
        protocol = protocol_override or profile.protocol
        if protocol not in self.SUPPORTED_PROTOCOLS:
            raise ProviderConfigurationError(f"Protocol provider '{protocol}' belum didukung.")
        runtime = ProviderRuntime(
            name=profile.name, base_url=base_url_override or profile.base_url,
            api_key=api_key_override or profile.api_key,
            default_model=profile.default_model, extra_body=profile.extra_body,
            capabilities=frozenset(capabilities_override) if capabilities_override is not None else profile.capabilities,
            attachment_modes=profile.attachment_modes,
            model_capabilities=frozenset(model_capabilities_override or ()),
            model_capabilities_known=bool(model_capabilities_known),
        )
        if protocol == "openai_responses":
            return OpenAIResponsesProvider(runtime)
        if protocol == "anthropic_messages":
            return AnthropicMessagesProvider(runtime)
        if protocol == "gemini_generate_content":
            return GeminiProvider(runtime)
        return OpenAICompatibleProvider(runtime)

    def profile(self, name=None):
        selected = name or self.settings.ai_default_provider
        return self._profiles.get(selected)
