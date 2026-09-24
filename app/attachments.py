from dataclasses import dataclass
from pathlib import Path
import base64, mimetypes
from aiogram.types import Message
from .config import get_settings

@dataclass
class Attachment:
    kind: str
    file_id: str
    filename: str | None
    mime_type: str | None
    path: str | None = None
    def data_url(self):
        if not self.path or not self.mime_type: return None
        return f"data:{self.mime_type};base64,{base64.b64encode(Path(self.path).read_bytes()).decode()}"

class AttachmentManager:
    def __init__(self,bot):
        self.bot=bot; self.settings=get_settings(); Path(self.settings.attachment_dir).mkdir(parents=True,exist_ok=True)
    async def collect(self,message:Message):
        items=[]
        if message.photo:
            o=message.photo[-1]; path=await self._download(o.file_id,f"{o.file_id}.jpg"); items.append(Attachment("image",o.file_id,"photo.jpg","image/jpeg",path))
        for attr,kind in [("document","document"),("audio","audio"),("video","video"),("voice","audio")]:
            o=getattr(message,attr,None)
            if o:
                mime=getattr(o,"mime_type",None) or mimetypes.guess_type(getattr(o,"file_name","") or "")[0]
                name=getattr(o,"file_name",None) or f"{o.file_id}.{kind}"; path=await self._download(o.file_id,f"{o.file_id}_{name}")
                items.append(Attachment(kind,o.file_id,name,mime,path))
        return items
    async def _download(self,file_id,filename):
        info=await self.bot.get_file(file_id); path=Path(self.settings.attachment_dir)/filename
        await self.bot.download(info,destination=path)
        if path.stat().st_size>self.settings.attachment_max_mb*1024*1024: path.unlink(missing_ok=True); raise ValueError(f"Attachment exceeds {self.settings.attachment_max_mb} MB")
        return str(path)
