import json
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from ..attachments import Attachment
from .attachments import AttachmentMapping
from .base import AIProvider, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from .registry_types import ProviderRuntime


class OpenAIResponsesProvider(AIProvider):
    """OpenAI Responses-compatible provider with native file uploads.

    This is intentionally a separate protocol: arbitrary OpenAI-compatible
    Chat Completions endpoints must not be assumed to expose /files or /responses.
    """

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

    def _base(self):
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        base = self.runtime.base_url.strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("Base URL provider tidak valid.")
        return base, {"Authorization": f"Bearer {self.runtime.api_key}"}

    def _payload(self, messages, model=None, stream=False):
        selected = model or self.runtime.default_model
        if not selected:
            raise ProviderConfigurationError("Model provider belum dikonfigurasi.")
        payload = dict(self.runtime.extra_body)
        payload["model"] = selected
        payload["input"] = self._to_input(messages)
        payload["stream"] = bool(stream)
        return payload

    def _to_input(self, messages):
        result = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if isinstance(content, str):
                result.append({"role": role, "content": [{"type": "input_text", "text": content}]})
                continue
            parts = []
            for part in content or []:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                if kind == "text":
                    parts.append({"type": "input_text", "text": str(part.get("text", ""))})
                elif kind == "image_url":
                    url = (part.get("image_url") or {}).get("url")
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
                elif kind == "input_file":
                    parts.append(part)
            result.append({"role": role, "content": parts})
        return result

    async def prepare_attachments(self, attachments, text=""):
        parts = [{"type": "text", "text": text}] if text else []
        notes = []
        for item in attachments:
            if item.is_image and item.data_url():
                parts.append({"type": "image_url", "image_url": {"url": item.data_url()}})
                notes.append(f"[Image: {item.filename}; MIME: {item.mime_type}; size: {item.size} bytes]")
                continue
            file_id = await self.upload_attachment(item)
            if file_id:
                parts.append({"type": "input_file", "file_id": file_id})
                notes.append(f"[Native file: {item.filename or item.kind}; MIME: {item.mime_type or 'unknown'}]")
            else:
                fallback = self.map_attachments([item], "")
                parts.extend(fallback.parts)
                notes.append(fallback.note)
        return AttachmentMapping(parts=parts, note="\n".join(notes))

    async def upload_attachment(self, attachment: Attachment):
        if not attachment.path:
            return None
        base, headers = self._base()
        headers = dict(headers)
        try:
            with open(attachment.path, "rb") as fh:
                files = {
                    "file": (
                        attachment.filename or "attachment",
                        fh,
                        attachment.mime_type or "application/octet-stream",
                    )
                }
                data = {"purpose": "user_data"}
                async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                    response = await client.post(
                        base + "/files", headers=headers, data=data, files=files
                    )
        except httpx.TimeoutException as exc:
            raise ProviderError("Upload file ke provider timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider file API tidak dapat dihubungi.") from exc
        if response.is_error:
            raise self._http_error(response)
        try:
            data = response.json()
            file_id = data.get("id")
        except ValueError as exc:
            raise ProviderError("Provider file API mengembalikan JSON yang tidak valid.") from exc
        if not file_id:
            raise ProviderError("Provider file API tidak mengembalikan file ID.")
        return str(file_id)

    def supports_native_upload(self, attachment):
        return bool(attachment.path)

    async def chat(self, messages, model=None):
        base, headers = self._base()
        headers["Content-Type"] = "application/json"
        payload = self._payload(messages, model)
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.post(base + "/responses", headers=headers, json=payload)
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
        text = data.get("output_text")
        if not text:
            text = self._extract_output(data)
        return ProviderResponse(str(text or ""), data)

    async def stream(self, messages, model=None) -> AsyncIterator[str]:
        base, headers = self._base()
        headers["Content-Type"] = "application/json"
        payload = self._payload(messages, model, stream=True)
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                async with client.stream("POST", base + "/responses", headers=headers, json=payload) as response:
                    if response.is_error:
                        body = await response.aread()
                        raise self._http_error_bytes(response.status_code, body)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "response.output_text.delta":
                            delta = event.get("delta", "")
                            if delta:
                                yield delta
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("Provider AI timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider AI tidak dapat dihubungi.") from exc

    def _extract_output(self, data):
        chunks = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
        return "".join(chunks)

    def _http_error(self, response):
        try:
            data = response.json()
            error = data.get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else ""
        except Exception:
            detail = ""
        return ProviderError(
            f"Provider AI HTTP {response.status_code}" + (f": {str(detail)[:300]}" if detail else ".")
        )

    def _http_error_bytes(self, status, body):
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            error = data.get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else ""
        except Exception:
            detail = ""
        return ProviderError(
            f"Provider AI HTTP {status}" + (f": {str(detail)[:300]}" if detail else ".")
        )
