import json
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from ..attachments import Attachment
from .attachments import AttachmentMapping
from .base import AIProvider, ModelInfo, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from .registry_types import ProviderRuntime


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, profile=None):
        from ..config import get_settings
        self.settings = get_settings()
        self.runtime = profile or ProviderRuntime(
            name="default",
            base_url=self.settings.ai_base_url,
            api_key=self.settings.ai_api_key,
            default_model=self.settings.ai_model,
            extra_body={},
        )

    def _request(self, messages, model=None, stream=False):
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        selected = model or self.runtime.default_model
        if not selected:
            raise ProviderConfigurationError("Model provider belum dikonfigurasi.")
        base_url = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        url = base_url + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.runtime.api_key}", "Content-Type": "application/json"}
        payload = dict(self.runtime.extra_body)
        payload["model"] = selected
        payload["messages"] = messages
        payload["stream"] = bool(stream)
        return url, headers, payload

    async def list_models(self) -> list[str]:
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        base_url = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.get(
                    base_url + "/models",
                    headers={"Authorization": f"Bearer {self.runtime.api_key}"},
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
        models = data.get("data") or data.get("models") or []
        result = []
        for item in models:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and item.get("id"):
                result.append(str(item["id"]))

        return sorted(set(result))

    async def list_model_info(self) -> list[ModelInfo]:
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        base_url = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.get(base_url + "/models", headers={"Authorization": f"Bearer {self.runtime.api_key}"})
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
        for item in data.get("data") or data.get("models") or []:
            if isinstance(item, str):
                result.append(ModelInfo(id=item))
                continue
            if not isinstance(item, dict) or not item.get("id"):
                continue
            caps = set()
            capabilities_known = isinstance(item.get("capabilities"), dict) or any(
                isinstance(item.get(key), (list, tuple)) for key in ("modalities", "input_modalities", "inputModalities")
            )
            raw = item.get("capabilities") or {}
            if isinstance(raw, dict):
                for key, value in raw.items():
                    key = str(key).lower()
                    if value is True and key in {"vision", "audio", "video", "file", "pdf", "document", "reasoning"}:
                        caps.add(key)
            for key in ("modalities", "input_modalities", "inputModalities"):
                values = item.get(key)
                if isinstance(values, (list, tuple)):
                    for value in values:
                        value = str(value).lower()
                        if value in {"image", "vision"}: caps.add("vision")
                        elif value in {"audio", "video", "file", "pdf", "document"}: caps.add("file" if value in {"file","pdf","document"} else value)
            result.append(ModelInfo(id=str(item["id"]), capabilities=frozenset(caps), display_name=str(item.get("name") or item.get("display_name") or ""), capabilities_known=capabilities_known))
        return sorted({item.id: item for item in result}.values(), key=lambda item: item.id)

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
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Provider AI tidak mengembalikan choices.")
        content = (choices[0].get("message") or {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ProviderResponse(str(content), data)

    async def stream(self, messages, model=None) -> AsyncIterator[str]:
        url, headers, payload = self._request(messages, model, stream=True)
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.is_error:
                        body = await response.aread()
                        try:
                            data = json.loads(body.decode("utf-8", errors="replace"))
                            detail = data.get("error", {}).get("message", "")
                        except Exception:
                            detail = ""
                        raise ProviderError(
                            f"Provider AI HTTP {response.status_code}" +
                            (f": {str(detail)[:300]}" if detail else ".")
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content", "")
                        if isinstance(delta, str) and delta:
                            yield delta
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

    async def prepare_attachments(self, attachments: list[Attachment], text: str = "") -> AttachmentMapping:
        return self.map_attachments(attachments, text)
