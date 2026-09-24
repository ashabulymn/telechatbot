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


class GeminiProvider(AIProvider):
    """Native Google Gemini generateContent/streamGenerateContent adapter."""

    def __init__(self, runtime: ProviderRuntime):
        from ..config import get_settings
        self.settings = get_settings()
        self.runtime = runtime

    def _base(self):
        base = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        return base

    def _contents(self, messages):
        contents = []
        system_parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            parts = self._parts(content)
            if role == "system":
                system_parts.extend(parts)
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": parts})
        return contents, system_parts

    def _parts(self, content):
        if isinstance(content, str):
            return [{"text": content}]
        if not isinstance(content, list):
            return [{"text": str(content)}]
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append({"text": str(item.get("text", ""))})
            elif item.get("type") == "image_url":
                url = ((item.get("image_url") or {}).get("url") or "")
                if url.startswith("data:") and ";base64," in url:
                    header, encoded = url.split(";base64,", 1)
                    parts.append({
                        "inlineData": {
                            "mimeType": header[5:] or "image/jpeg",
                            "data": encoded,
                        }
                    })
        return parts or [{"text": ""}]

    def _request(self, messages, model=None, stream=False):
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        selected = model or self.runtime.default_model
        if not selected:
            raise ProviderConfigurationError("Model provider belum dikonfigurasi.")
        contents, system_parts = self._contents(messages)
        payload = dict(self.runtime.extra_body)
        payload["contents"] = contents
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        url = f"{self._base()}/models/{selected}:{'streamGenerateContent?alt=sse' if stream else 'generateContent'}"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.runtime.api_key}
        return url, headers, payload

    @staticmethod
    def _extract(data):
        text = []
        for candidate in data.get("candidates", []):
            content = candidate.get("content") or {}
            for part in content.get("parts", []):
                value = part.get("text")
                if isinstance(value, str):
                    text.append(value)
        return "".join(text)

    async def list_models(self) -> list[str]:
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.get(
                    f"{self._base()}/models",
                    headers={"x-goog-api-key": self.runtime.api_key},
                    params={"pageSize": 1000},
                )
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
        result = []
        for item in data.get("models") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("baseModelId") or item.get("name") or "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            actions = item.get("supportedGenerationMethods") or item.get("supportedActions") or []
            if name and (not actions or "generateContent" in actions):
                result.append(name)
        return sorted(set(result))

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
        return ProviderResponse(self._extract(data), data)

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
                        message = str(data.get("error", {}).get("message", ""))[:300]
                        raise ProviderError(f"Provider AI HTTP {response.status_code}" + (f": {message}" if message else "."))
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            data = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        text = self._extract(data)
                        if text:
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
            error = data.get("error", {})
            detail = str(error.get("message", ""))[:300] if isinstance(error, dict) else ""
        except Exception:
            pass
        return ProviderError(f"Provider AI HTTP {response.status_code}" + (f": {detail}" if detail else "."))

    async def prepare_attachments(self, attachments: list[Attachment], text: str = "", progress=None) -> AttachmentMapping:
        return self.map_attachments(attachments, text)
