import logging
from aiogram import Bot,Dispatcher,Router,F
from aiogram.filters import Command
from aiogram.types import Message
from .attachments import AttachmentManager
from .config import get_settings
from .db import Database
from .providers import OpenAICompatibleProvider
log=logging.getLogger(__name__); router=Router()
class BotApp:
    def __init__(self,bot:Bot,db:Database): self.bot=bot; self.db=db; self.attachments=AttachmentManager(bot); self.provider=OpenAICompatibleProvider(); self.settings=get_settings()
    async def handle_message(self,message:Message):
        if not message.from_user: return
        uid=message.from_user.id; text=message.text or message.caption or ""; attachments=await self.attachments.collect(message)
        content=text
        if attachments:
            parts=[]
            if text: parts.append({"type":"text","text":text})
            for a in attachments:
                if a.kind=="image" and a.data_url(): parts.append({"type":"image_url","image_url":{"url":a.data_url()}})
            content=parts if parts else (text or "\n".join(f"[{a.kind}: {a.filename}]" for a in attachments))
        if not content: await message.answer("Kirim teks atau lampiran yang ingin diproses."); return
        await self.db.add_message(uid,"user",text or "[attachment]"); history=await self.db.history(uid,self.settings.ai_max_history_messages)
        if history: history[-1]["content"]=content
        messages=[{"role":"system","content":self.settings.ai_system_prompt}]+history
        try: result=await self.provider.chat(messages,await self.db.get_model(uid))
        except Exception as exc: log.exception("AI request failed"); await message.answer(f"AI error: {exc}"); return
        await self.db.add_message(uid,"assistant",result.text); await message.answer(result.text[:4096] or "Provider mengembalikan respons kosong.")
def register_handlers(dp:Dispatcher,app:BotApp):
    @router.message(Command("start"))
    async def start(m:Message): await m.answer("TeleChatBot aktif. Kirim pesan untuk mulai chat.\n/model <nama> untuk memilih model.\n/clear untuk menghapus riwayat.")
    @router.message(Command("help"))
    async def help_cmd(m:Message): await m.answer("/start — mulai\n/model <model> — pilih model\n/clear — hapus riwayat\nKirim teks, foto, dokumen, audio, atau video.")
    @router.message(Command("clear"))
    async def clear(m:Message):
        if m.from_user: await app.db.clear(m.from_user.id)
        await m.answer("Riwayat percakapan dihapus.")
    @router.message(Command("model"))
    async def model(m:Message):
        if not m.from_user:return
        p=(m.text or "").split(maxsplit=1)
        if len(p)==1: await m.answer(f"Model saat ini: {await app.db.get_model(m.from_user.id) or app.settings.ai_model or '(default provider)'}"); return
        await app.db.set_model(m.from_user.id,p[1].strip()); await m.answer(f"Model diubah ke: {p[1].strip()}")
    @router.message(F.text|F.photo|F.document|F.audio|F.video|F.voice)
    async def any_message(m:Message): await app.handle_message(m)
    dp.include_router(router)
