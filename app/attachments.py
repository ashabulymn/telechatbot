from dataclasses import dataclass
from pathlib import Path
import base64
import mimetypes
import re
import uuid

from aiogram.types import Message

from .config import get_settings


@dataclass
class Attachment:
    kind: str
    file_id: str
    filename: str | None
    mime_type: str | None
    path: str | None = None

    @property
    def is_image(self):
        return bool(self.mime_type and self.mime_type.startswith("image/"))

    @property
    def size(self):
        return Path(self.path).stat().st_size if self.path and Path(self.path).exists() else 0

    def data_url(self):
        if not self.path or not self.mime_type or not self.is_image:
            return None
        return f"data:{self.mime_type};base64,{base64.b64encode(Path(self.path).read_bytes()).decode()}"

    def metadata(self):
        return {
            "kind": self.kind,
            "file_id": self.file_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "path": self.path,
        }


class AttachmentManager:
    def __init__(self, bot):
        self.bot = bot
        self.settings = get_settings()
        Path(self.settings.attachment_dir).mkdir(parents=True, exist_ok=True)

    async def collect(self, message: Message):
        items = []
        try:
            if message.photo:
                obj = message.photo[-1]
                path = await self._download(obj.file_id, "photo.jpg", getattr(obj, "file_size", None))
                items.append(Attachment("image", obj.file_id, "photo.jpg", "image/jpeg", path))

            for attr, kind in [
                ("document", "document"),
                ("audio", "audio"),
                ("video", "video"),
                ("voice", "audio"),
            ]:
                obj = getattr(message, attr, None)
                if not obj:
                    continue
                original = getattr(obj, "file_name", None) or f"{kind}_{obj.file_id}"
                mime = getattr(obj, "mime_type", None) or mimetypes.guess_type(original)[0]
                safe_name = self._safe_filename(original)
                path = await self._download(obj.file_id, safe_name, getattr(obj, "file_size", None))
                items.append(Attachment(kind, obj.file_id, original, mime, path))
            return items
        except Exception:
            self.cleanup(items)
            raise

    def _safe_filename(self, filename):
        name = Path(filename).name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        return f"{uuid.uuid4().hex[:12]}_{name or 'attachment'}"

    async def _download(self, file_id, filename, declared_size=None):
        max_bytes = self.settings.attachment_max_mb * 1024 * 1024
        if declared_size is not None and declared_size > max_bytes:
            raise ValueError(f"Attachment exceeds {self.settings.attachment_max_mb} MB")

        info = await self.bot.get_file(file_id)
        remote_size = getattr(info, "file_size", None)
        if remote_size is not None and remote_size > max_bytes:
            raise ValueError(f"Attachment exceeds {self.settings.attachment_max_mb} MB")

        path = Path(self.settings.attachment_dir) / filename
        await self.bot.download(info, destination=path)
        actual_size = path.stat().st_size
        if actual_size > max_bytes:
            path.unlink(missing_ok=True)
            raise ValueError(f"Attachment exceeds {self.settings.attachment_max_mb} MB")
        return str(path)

    def cleanup(self, attachments):
        for item in attachments:
            if item.path:
                try:
                    Path(item.path).unlink(missing_ok=True)
                except OSError:
                    pass
