import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message
from .attachments import AttachmentManager
from .config import get_settings
from .db import Database
from .file_parser import FileParser
from .providers import OpenAICompatibleProvider
from .providers.errors import ProviderError

log = logging.getLogger(__name__)
router = Router()

class BotApp:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.attachments = AttachmentManager(bot)
        self.parser = FileParser(get_settings().attachment_max_prompt_chars)
        self.provider = OpenAICompatibleProvider()
        self.settings = get_settings()

    async def handle_message(self, message: Message):
        if not message.from_user:
            return
        uid = message.from_user.id
        text = message.text or message.caption or ""
        try:
            attachments = await self.attachments.collect(message)
        except ValueError as exc:
            await message.answer(str(exc))
            return

        content = text
        if attachments:
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            for item in attachments:
                if item.kind == "image" and item.data_url():
                    parts.append({"type": "image_url", "image_url": {"url": item.data_url()}})
                    continue
                extracted = self.parser.extract(item.path, item.mime_type, item.filename) if item.path else None
                if extracted:
                    parts.append({
                        "type": "text",
                        "text": f"\n[File: {item.filename}]\n{extracted}",
                    })
                else:
                    parts.append({
                        "type": "text",
                        "text": f"\n[Attachment: {item.filename or item.kind}; MIME: {item.mime_type or 'unknown'}]",
                    })
            content = parts

        if not content:
            await message.answer("Kirim teks atau lampiran yang ingin diproses.")
            return

        await self.db.add_message(uid, "user", text or "[attachment]")
        history = await self.db.history(uid, self.settings.ai_max_history_messages)
        if history:
            history[-1]["content"] = content

        messages = [{"role": "system", "content": self.settings.ai_system_prompt}] if self.settings.ai_system_prompt else []
        messages.extend(history)

        try:
            result = await self.provider.chat(messages, await self.db.get_model(uid))
        except ProviderError as exc:
            await message.answer(str(exc))
            return
        except Exception:
            log.exception("Unexpected AI request failure")
            await message.answer("Terjadi error saat menghubungi provider AI.")
            return

        await self.db.add_message(uid, "assistant", result.text)
        await message.answer(result.text[:4096] or "Provider mengembalikan respons kosong.")

def register_handlers(dp: Dispatcher, app: BotApp):
    @router.message(Command("start"))
    async def start(message: Message):
        await message.answer(
            "TeleChatBot aktif.\n"
            "Kirim pesan untuk mulai chat.\n"
            "/model <nama> — pilih model\n"
            "/status — lihat konfigurasi aktif\n"
            "/clear — hapus riwayat."
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer(
            "/start — mulai\n"
            "/model <model> — pilih model\n"
            "/status — status provider\n"
            "/clear — hapus riwayat\n"
            "Kirim teks, foto, PDF, dokumen, audio, atau video."
        )

    @router.message(Command("status"))
    async def status(message: Message):
        model = await app.db.get_model(message.from_user.id) if message.from_user else None
        selected = model or app.settings.ai_model or "(belum diset)"
        await message.answer(
            f"Provider: OpenAI-compatible\n"
            f"Base URL: {app.settings.ai_base_url}\n"
            f"Model: {selected}\n"
            f"Mode: {app.settings.telegram_mode}"
        )

    @router.message(Command("clear"))
    async def clear(message: Message):
        if message.from_user:
            await app.db.clear(message.from_user.id)
        await message.answer("Riwayat percakapan dihapus.")

    @router.message(Command("model"))
    async def model(message: Message):
        if not message.from_user:
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = await app.db.get_model(message.from_user.id)
            await message.answer(f"Model saat ini: {current or app.settings.ai_model or '(default provider)'}")
            return
        value = parts[1].strip()
        await app.db.set_model(message.from_user.id, value)
        await message.answer(f"Model diubah ke: {value}")

    @router.message(F.text | F.photo | F.document | F.audio | F.video | F.voice)
    async def any_message(message: Message):
        await app.handle_message(message)

    dp.include_router(router)
