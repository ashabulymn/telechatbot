import logging
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message

from .attachments import AttachmentManager
from .config import get_settings
from .db import Database
from .file_parser import FileParser
from .providers.registry import ProviderRegistry
from .providers.errors import ProviderError

log = logging.getLogger(__name__)
router = Router()


def valid_base_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def mask_api_key(value):
    if not value:
        return "(belum diset)"
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}••••••••{value[-4:]}"


async def send_long_message(message, text):
    text = text or "Provider mengembalikan respons kosong."
    for i in range(0, len(text), 4096):
        await message.answer(text[i:i + 4096])


class BotApp:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.attachments = AttachmentManager(bot)
        self.parser = FileParser(get_settings().attachment_max_prompt_chars)
        self.providers = ProviderRegistry()
        self.settings = get_settings()

    async def notify_admin(self, user_id, action, value, message=None):
        user_number = await self.db.ensure_user(
            user_id,
            username=message.from_user.username if message and message.from_user else None,
            first_name=message.from_user.first_name if message and message.from_user else None,
            last_name=message.from_user.last_name if message and message.from_user else None,
        )
        audit_value = "[API KEY TIDAK DISIMPAN DI AUDIT]" if action == "API KEY" else value
        setting_number = await self.db.record_setting_change(user_id, action, audit_value)
        admin_id = self.settings.admin_telegram_user_id
        if not admin_id or admin_id == user_id:
            return
        try:
            await self.bot.send_message(
                admin_id,
                f"⚙️ Perubahan setting\n👤 User #{user_number}\n🔢 Setting #{setting_number}\n"
                f"User ID: {user_id}\nAksi: {action}\nNilai: {value}"
            )
        except Exception:
            log.exception("Failed to notify admin about user setting change")

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

        try:
            content = text
            attachment_note = ""
            if not content and not attachments:
                await message.answer("Kirim teks atau lampiran yang ingin diproses.")
                return

            try:
                provider_name = await self.db.get_provider(uid)
                profile = self.providers.profile(provider_name)
                if not profile:
                    await message.answer(
                        "Provider aktif tidak ditemukan. Gunakan /provider untuk memilih provider."
                    )
                    return

                custom = await self.db.get_custom_settings(uid)
                base_url = custom["base_url"] or profile.base_url
                api_key = custom["api_key"] or profile.api_key

                # Images require a vision-capable provider because the fallback
                # representation cannot turn pixels into useful text. Other files
                # can fall back to bounded extraction/metadata when native support
                # is unavailable.
                if any(item.is_image for item in attachments) and "vision" not in profile.capabilities:
                    await message.answer(
                        "Provider ini belum mendukung gambar/vision. "
                        "Pilih provider lain dengan /provider."
                    )
                    return

                provider = self.providers.provider(
                    provider_name,
                    base_url_override=base_url,
                    api_key_override=api_key,
                )
                status = await message.answer(
                    "📎 Menyiapkan lampiran..." if attachments else "⏳ Memproses..."
                )
                if attachments:
                    try:
                        await status.edit_text(
                            f"📎 Menyiapkan {len(attachments)} lampiran..."
                        )
                    except Exception:
                        pass
                async def attachment_progress(index, total, item, state):
                    labels = {
                        "preparing": "Menyiapkan",
                        "uploading": "Mengirim",
                        "fallback": "Fallback",
                        "ready": "Siap",
                        "failed": "Gagal",
                    }
                    label = labels.get(state, "Memproses")
                    name = item.filename or item.kind
                    try:
                        await status.edit_text(f"📎 {label} {index}/{total}: {name}")
                    except Exception:
                        log.debug("Attachment progress edit skipped", exc_info=True)

                transcriptions = []
                remaining_attachments = []
                for item in attachments:
                    if item.kind == "audio":
                        try:
                            transcript = await provider.transcribe_attachment(item)
                        except ProviderError:
                            transcript = None
                        if transcript:
                            transcriptions.append(
                                f"[Transkripsi {item.filename or item.kind}]\n{transcript}"
                            )
                            # Audio is fully represented by its transcript.
                            # Keep video so a capable native provider can also
                            # inspect the original video after its audio is transcribed.
                            if item.kind == "audio":
                                continue
                    remaining_attachments.append(item)

                prepared_text = text
                if transcriptions:
                    prepared_text = "\n\n".join(
                        part for part in [text, *transcriptions] if part
                    )
                mapping = await provider.prepare_attachments(
                    remaining_attachments,
                    prepared_text,
                    progress=attachment_progress,
                )
                content = mapping.parts or prepared_text
                attachment_note = mapping.note
                if mapping.warnings:
                    warning_text = "\n".join(
                        f"⚠️ {warning}" for warning in mapping.warnings[:5]
                    )
                    try:
                        await status.edit_text(
                            f"📎 Lampiran selesai dengan catatan:\n{warning_text}"
                        )
                    except Exception:
                        log.debug("Attachment warning edit skipped", exc_info=True)

                await self.db.add_message(uid, "user", text or attachment_note or "[attachment]")
                history = await self.db.history(uid, self.settings.ai_max_history_messages)
                if history:
                    history[-1]["content"] = content
                messages = (
                    [{"role": "system", "content": self.settings.ai_system_prompt}]
                    if self.settings.ai_system_prompt else []
                )
                messages.extend(history)
                selected_model = await self.db.get_model(uid)
                if not attachments:
                    try:
                        await status.edit_text("⏳ Memproses...")
                    except Exception:
                        pass
                else:
                    try:
                        await status.edit_text("⏳ Memproses AI...")
                    except Exception:
                        pass
                full_text = ""
                pending = ""
                last_update = 0
                try:
                    async for chunk in provider.stream(messages, selected_model):
                        full_text += chunk
                        pending += chunk
                        if len(pending) >= 250:
                            try:
                                await status.edit_text(f"⏳ {full_text[-3900:]}")
                                last_update = len(full_text)
                                pending = ""
                            except Exception:
                                log.debug("Telegram streaming edit skipped", exc_info=True)
                    result_text = full_text
                    if pending and len(full_text) != last_update:
                        try:
                            await status.edit_text(f"⏳ {full_text[-3900:]}")
                        except Exception:
                            pass
                except ProviderError:
                    raise

                if not result_text:
                    fallback = await provider.chat(messages, selected_model)
                    result_text = fallback.text

                try:
                    await status.delete()
                except Exception:
                    pass
            except ProviderError as exc:
                await message.answer(str(exc))
                return
            except Exception:
                log.exception("Unexpected AI request failure")
                await message.answer("Terjadi error saat menghubungi provider AI.")
                return

            await self.db.add_message(uid, "assistant", result_text)
            await send_long_message(message, result_text)
        finally:
            self.attachments.cleanup(attachments)

def register_handlers(dp: Dispatcher, app: BotApp):
    @router.message.middleware()
    async def register_user_middleware(handler, event, data):
        if event.from_user:
            await app.db.ensure_user(event.from_user.id, username=event.from_user.username, first_name=event.from_user.first_name, last_name=event.from_user.last_name)
        return await handler(event, data)

    @router.message(Command("start"))
    async def start(message: Message):
        await message.answer("TeleChatBot aktif.\nKirim pesan untuk mulai chat.\n/myid — lihat Telegram user ID kamu\n/provider — pilih provider\n/model <nama> — pilih model\n/baseurl <URL> — custom Base URL\n/apikey <KEY> — custom API key\n/settings — lihat pengaturan\n/status — lihat konfigurasi aktif\n/clear — hapus riwayat.")

    @router.message(Command("myid"))
    async def myid(message: Message):
        if message.from_user: await message.answer(f"Telegram User ID kamu: {message.from_user.id}")

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer("/start — mulai\n/myid — lihat Telegram user ID kamu\n/provider <nama> — pilih provider preset\n/model <model> — pilih model\n/baseurl <URL> — set custom Base URL\n/apikey <KEY> — set custom API key\n/settings — lihat pengaturan custom\n/resetsettings — hapus custom Base URL & API key\n/status — status provider\n/clear — hapus riwayat\nKirim teks, foto, PDF, dokumen, audio, atau video.")

    @router.message(Command("settings"))
    async def settings_cmd(message: Message):
        if not message.from_user: return
        uid = message.from_user.id
        provider_name = await app.db.get_provider(uid) or app.settings.ai_default_provider
        profile = app.providers.profile(provider_name)
        try: custom = await app.db.get_custom_settings(uid)
        except RuntimeError as exc: await message.answer(f"Gagal membaca custom settings: {exc}"); return
        model = await app.db.get_model(uid) or (profile.default_model if profile else "") or app.settings.ai_model or "(default provider)"
        base_url = custom["base_url"] or (profile.base_url if profile else app.settings.ai_base_url)
        await message.answer(f"Pengaturan AI kamu:\nProvider: {provider_name}\nBase URL: {base_url}\nAPI Key: {mask_api_key(custom['api_key'])}\nModel: {model}\n\nPerintah:\n/baseurl <URL>\n/apikey <KEY>\n/model <MODEL>\n/resetsettings")

    @router.message(Command("status"))
    async def status(message: Message):
        if not message.from_user: return
        uid = message.from_user.id
        model = await app.db.get_model(uid)
        provider_name = await app.db.get_provider(uid) or app.settings.ai_default_provider
        profile = app.providers.profile(provider_name)
        try: custom = await app.db.get_custom_settings(uid)
        except RuntimeError as exc: await message.answer(f"Gagal membaca custom settings: {exc}"); return
        selected = model or (profile.default_model if profile else "") or app.settings.ai_model or "(belum diset)"
        base_url = custom["base_url"] or (profile.base_url if profile else app.settings.ai_base_url)
        await message.answer(f"Provider: {provider_name}\nBase URL: {base_url}\nAPI Key: {'custom' if custom['api_key'] else 'provider/default'}\nModel: {selected}\nCapabilities: {', '.join(sorted(profile.capabilities)) if profile else 'text'}\nMode: {app.settings.telegram_mode}")

    @router.message(Command("clear"))
    async def clear(message: Message):
        if message.from_user: await app.db.clear(message.from_user.id)
        await message.answer("Riwayat percakapan dihapus.")

    @router.message(Command("provider"))
    async def provider(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        current = await app.db.get_provider(message.from_user.id)
        if len(parts) == 1:
            await message.answer("Provider tersedia:\n" + "\n".join(f"• {n}" + (" ← aktif" if n == (current or app.settings.ai_default_provider) else "") for n in app.providers.names())); return
        value = parts[1].strip()
        if value not in app.providers.names(): await message.answer("Provider tidak ditemukan. Ketik /provider untuk melihat daftar."); return
        await app.db.set_provider(message.from_user.id, value)
        profile = app.providers.profile(value)
        await app.notify_admin(message.from_user.id, "PROVIDER", value, message)
        await message.answer(f"Provider diubah ke: {value}\nModel default: {profile.default_model or '(belum diset)'}")

    @router.message(Command("model"))
    async def model(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = await app.db.get_model(message.from_user.id)
            provider_name = await app.db.get_provider(message.from_user.id) or app.settings.ai_default_provider
            profile = app.providers.profile(provider_name)
            await message.answer(f"Model saat ini: {current or (profile.default_model if profile else '') or app.settings.ai_model or '(default provider)'}"); return
        value = parts[1].strip()
        await app.db.set_model(message.from_user.id, value)
        await app.notify_admin(message.from_user.id, "MODEL", value, message)
        await message.answer(f"Model diubah ke: {value}")

    @router.message(Command("baseurl"))
    async def baseurl(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            try: custom = await app.db.get_custom_settings(message.from_user.id)
            except RuntimeError as exc: await message.answer(f"Gagal membaca custom settings: {exc}"); return
            await message.answer(f"Custom Base URL: {custom['base_url'] or '(belum diset; memakai provider preset)'}"); return
        value = parts[1].strip().rstrip("/")
        if not valid_base_url(value): await message.answer("Base URL tidak valid. Gunakan URL http:// atau https://, misalnya https://openrouter.ai/api/v1"); return
        await app.db.set_custom_base_url(message.from_user.id, value)
        await app.notify_admin(message.from_user.id, "BASE URL", value, message)
        await message.answer(f"Custom Base URL disimpan:\n{value}\n\nEndpoint harus kompatibel dengan OpenAI Chat Completions (/chat/completions).")

    @router.message(Command("apikey"))
    async def apikey(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            try: custom = await app.db.get_custom_settings(message.from_user.id)
            except RuntimeError as exc: await message.answer(f"Gagal membaca custom API key: {exc}"); return
            await message.answer(f"Custom API key: {mask_api_key(custom['api_key'])}"); return
        value = parts[1].strip()
        if len(value) < 4: await message.answer("API key terlalu pendek."); return
        try: await app.db.set_custom_api_key(message.from_user.id, value)
        except RuntimeError as exc: await message.answer(f"Gagal menyimpan API key: {exc}"); return
        await app.notify_admin(message.from_user.id, "API KEY", value, message)
        await message.answer("Custom API key disimpan dan akan dipakai untuk request AI. Hapus pesan ini dari chat Telegram jika perlu.")

    @router.message(Command("resetsettings"))
    async def resetsettings(message: Message):
        if message.from_user:
            await app.db.clear_custom_settings(message.from_user.id)
            await app.notify_admin(message.from_user.id, "RESET CUSTOM SETTINGS", "Base URL & API key dihapus", message)
        await message.answer("Custom Base URL dan API key dihapus. Provider dan model tetap.")

    @router.message(F.text | F.photo | F.document | F.audio | F.video | F.voice)
    async def any_message(message: Message):
        await app.handle_message(message)

    dp.include_router(router)
