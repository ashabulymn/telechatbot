import base64
import json
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from ..attachments import Attachment
from .attachments import AttachmentMapping
from .base import AIProvider, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from .registry_types import ProviderRuntime


class AnthropicMessagesProvider(AIProvider):
    """Anthropic Messages API adapter."""

    API_VERSION = "2023-06-01"

    def __init__(self, runtime: ProviderRuntime):
        from ..config import get_settings
        self.settings = get_settings()
        self.runtime = runtime

    def _url(self):
        base = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")

    def _split_messages(self, messages):
        system = None
        converted = []
        for item in messages:
            role = item.get("role", "user")
            content = item.get("content", "")
            if role == "system":
                system = str(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            converted.append({"role": role, "content": self._content(content)})
        return system, converted

    def _content(self, content):
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append({"type": "text", "text": str(part.get("text", ""))})
            elif part.get("type") == "image_url":
                url = ((part.get("image_url") or {}).get("url") or "")
                if url.startswith("data:") and ";base64," in url:
                    header, encoded = url.split(";base64,", 1)
                    media_type = header[5:] or "image/jpeg"
                    parts.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    })
        return parts or ""

    def _request(self, messages, model=None, stream=False):
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        selected = model or self.runtime.default_model
        if not selected:
            raise ProviderConfigurationError("Model provider belum dikonfigurasi.")
        system, converted = self._split_messages(messages)
        payload = dict(self.runtime.extra_body)
        payload.update({"model": selected, "messages": converted, "max_tokens": payload.pop("max_tokens", 4096), "stream": bool(stream)})
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": self.runtime.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        return self._url(), headers, payload

    async def list_models(self) -> list[str]:
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        base = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        headers = {"x-api-key": self.runtime.api_key, "anthropic-version": self.API_VERSION}
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("Daftar model provider timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider model tidak dapat dihubungi.") from exc
        if response.is_error:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Provider model mengembalikan JSON yang tidak valid.") from exc
        models = data.get("data") or []
        return sorted({str(item["id"]) for item in models if isinstance(item, dict) and item.get("id")})

    async def chat(self, messages, model=None):
        url, headers, payload = self._request(messages, model)
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError("Provider AI timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider AI tidak dapat dihubungi.") from exc
        if response.is_error:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Provider AI mengembalikan JSON yang tidak valid.") from exc
        content = "".join(
            block.get("text", "") for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return ProviderResponse(content, data)

    async def stream(self, messages, model=None) -> AsyncIterator[str]:
        url, headers, payload = self._request(messages, model, stream=True)
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.is_error:
                        body = await response.aread()
                        try:
                            data = json.loads(body.decode("utf-8", errors="replace"))
                        except Exception:
                            data = {}
                        detail = ((data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else "")
                        raise ProviderError(
                            f"Provider AI HTTP {response.status_code}" +
                            (f": {str(detail)[:300]}" if detail else ".")
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta") or {}
                            text = delta.get("text")
                            if isinstance(text, str) and text:
                                yield text
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("Provider AI timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider AI tidak dapat dihubungi.") from exc

    def _http_error(self, response):
        detail = ""
        try:
            data = response.json()
            err = data.get("error", {})
            detail = str(err.get("message", ""))[:300] if isinstance(err, dict) else ""
        except Exception:
            pass
        return ProviderError(
            f"Provider AI HTTP {response.status_code}" + (f": {detail}" if detail else ".")
        )

    async def prepare_attachments(self, attachments: list[Attachment], text: str = "", progress=None) -> AttachmentMapping:
        return self.map_attachments(attachments, text)
