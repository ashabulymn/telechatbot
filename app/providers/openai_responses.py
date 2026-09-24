import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from ..attachments import Attachment
from .attachments import AttachmentMapping, ProgressCallback
from .base import AIProvider, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from .registry_types import ProviderRuntime


log = logging.getLogger(__name__)


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
            capabilities=frozenset({"text", "vision", "file", "audio", "video"}),
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

    async def prepare_attachments(
        self,
        attachments,
        text="",
        progress: ProgressCallback | None = None,
    ):
        parts = [{"type": "text", "text": text}] if text else []
        notes = []
        warnings = []
        remote_file_ids = []
        total = len(attachments)

        try:
            for index, item in enumerate(attachments, 1):
                filename = item.filename or item.kind
                if progress:
                    await progress(index, total, item, "preparing")

                if item.is_image and item.data_url():
                    parts.append({"type": "image_url", "image_url": {"url": item.data_url()}})
                    notes.append(
                        f"[Image: {filename}; MIME: {item.mime_type}; size: {item.size} bytes]"
                    )
                    if progress:
                        await progress(index, total, item, "ready")
                    continue

                file_id = None
                if self.supports_native_upload(item):
                    try:
                        if progress:
                            await progress(index, total, item, "uploading")
                        file_id = await self.upload_attachment(item)
                    except Exception as exc:
                        warning = f"{filename}: upload native gagal ({str(exc)[:180]}). Dipakai fallback."
                        warnings.append(warning)
                        if progress:
                            await progress(index, total, item, "fallback")

                if file_id:
                    parts.append({"type": "input_file", "file_id": file_id})
                    remote_file_ids.append(file_id)
                    notes.append(
                        f"[Native file: {filename}; MIME: {item.mime_type or 'unknown'}]"
                    )
                    if progress:
                        await progress(index, total, item, "ready")
                    continue

                try:
                    fallback = self.map_attachments([item], "")
                    parts.extend(fallback.parts)
                    notes.append(fallback.note)
                    warnings.extend(fallback.warnings)
                    if progress:
                        await progress(index, total, item, "ready")
                except Exception as exc:
                    warning = f"{filename}: lampiran dilewati ({str(exc)[:180]})."
                    warnings.append(warning)
                    if progress:
                        await progress(index, total, item, "failed")

        except BaseException:
            if remote_file_ids:
                await self.cleanup_attachments(
                    AttachmentMapping(
                        parts=[],
                        note="",
                        remote_file_ids=list(remote_file_ids),
                    )
                )
            raise

        return AttachmentMapping(
            parts=parts,
            note="\n".join(notes),
            warnings=warnings,
            remote_file_ids=remote_file_ids,
        )

    async def cleanup_attachments(self, mapping):
        if not mapping.remote_file_ids or "file_cleanup" not in self.runtime.capabilities:
            return
        try:
            base, headers = self._base()
        except ProviderError:
            return
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            for file_id in mapping.remote_file_ids:
                try:
                    response = await client.delete(
                        f"{base}/files/{file_id}", headers=headers
                    )
                    if response.is_error:
                        log.warning(
                            "Provider file cleanup failed: status=%s file_id=%s",
                            response.status_code,
                            file_id,
                        )
                except httpx.HTTPError:
                    log.warning(
                        "Provider file cleanup request failed: file_id=%s",
                        file_id,
                        exc_info=True,
                    )

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

    def attachment_mode(self, attachment):
        return self.runtime.attachment_modes.get(attachment.kind, "auto")

    def supports_native_upload(self, attachment):
        mode = self.attachment_mode(attachment)
        if mode in {"fallback", "transcribe", "disabled"}:
            return False
        if not attachment.path or "file" not in self.runtime.capabilities:
            return False
        if self.runtime.model_capabilities_known:
            if attachment.is_image and "vision" not in self.runtime.model_capabilities:
                return False
            if attachment.kind == "audio" and "audio" not in self.runtime.model_capabilities:
                return False
            if attachment.kind == "video" and "video" not in self.runtime.model_capabilities:
                return False
            if attachment.kind in {"document", "pdf"} and "file" not in self.runtime.model_capabilities:
                return False
        if attachment.kind == "audio":
            return "audio" in self.runtime.capabilities
        if attachment.kind == "video":
            return "video" in self.runtime.capabilities
        return True

    def supports_transcription(self, attachment):
        return (
            bool(attachment.path)
            and attachment.kind in {"audio", "video"}
            and self.attachment_mode(attachment) in {"auto", "transcribe"}
            and "transcription" in self.runtime.capabilities
        )

    async def transcribe_attachment(self, attachment):
        if not self.supports_transcription(attachment):
            return None
        if not attachment.path or attachment.kind not in {"audio", "video"}:
            return None

        source_path = attachment.path
        temporary_audio = None
        if attachment.kind == "video":
            if not self.settings.ai_video_transcription_enabled:
                return None
            fd, temporary_audio = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            try:
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", attachment.path,
                    "-vn", "-t", str(self.settings.ai_transcription_max_seconds),
                    "-ac", "1", "-ar", "16000", "-b:a", "64k",
                    "-y", temporary_audio,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=max(30, self.settings.ai_transcription_max_seconds + 30),
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return None
                if process.returncode != 0 or not Path(temporary_audio).exists():
                    return None
                source_path = temporary_audio
            except (OSError, asyncio.TimeoutError):
                return None

        base, headers = self._base()
        try:
            with open(source_path, "rb") as fh:
                files = {
                    "file": (
                        attachment.filename or ("video-audio.mp3" if attachment.kind == "video" else "audio"),
                        fh,
                        "audio/mpeg",
                    )
                }
                data = {"model": self.settings.ai_transcription_model}
                async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                    response = await client.post(
                        base + "/audio/transcriptions",
                        headers=headers,
                        data=data,
                        files=files,
                    )
        except httpx.TimeoutException as exc:
            raise ProviderError("Transkripsi audio/video timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider audio API tidak dapat dihubungi.") from exc
        finally:
            if temporary_audio:
                Path(temporary_audio).unlink(missing_ok=True)

        if response.is_error:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Provider audio API mengembalikan JSON yang tidak valid.") from exc
        text = data.get("text")
        return str(text).strip() if text else None

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
