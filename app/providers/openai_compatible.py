import json
from typing import AsyncIterator

import httpx

from .base import AIProvider, ProviderResponse
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
        if not self.runtime.base_url:
            raise ProviderConfigurationError("Base URL provider belum dikonfigurasi.")

        url = self.runtime.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.runtime.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": selected, "messages": messages}
        payload.update(self.runtime.extra_body)
        if stream:
            payload["stream"] = True
        return url, headers, payload

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
                part.get("text", "")
                for part in content
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
                        response._content = body
                        raise self._http_error(response)
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
