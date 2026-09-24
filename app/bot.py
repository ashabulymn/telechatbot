import logging
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

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


def model_keyboard(models, current=None, page=0, per_page=8):
    start = page * per_page
    page_models = models[start:start + per_page]
    rows = [[InlineKeyboardButton(text=(('✓ ' if name == current else '') + name[:60]), callback_data='setmodel:' + str(start + i))] for i, name in enumerate(page_models)]
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text='← Sebelumnya', callback_data='modelpage:' + str(page - 1)))
    if start + per_page < len(models): nav.append(InlineKeyboardButton(text='Berikutnya →', callback_data='modelpage:' + str(page + 1)))
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

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

        provider = None
        mapping = None
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
                protocol = custom["protocol"] or profile.protocol
                capabilities = frozenset(custom["capabilities"] or profile.capabilities)

                provider = self.providers.provider(
                    provider_name,
                    base_url_override=base_url,
                    api_key_override=api_key,
                    protocol_override=protocol,
                    capabilities_override=capabilities,
                )

                if any(provider.attachment_mode(item) == "disabled" for item in attachments):
                    disabled = [item.filename or item.kind for item in attachments if provider.attachment_mode(item) == "disabled"]
                    await message.answer("Provider ini menonaktifkan lampiran: " + ", ".join(disabled[:5]))
                    return

                # Images require a vision-capable provider because the fallback
                # representation cannot turn pixels into useful text. Other files
                # can fall back to bounded extraction/metadata when native support
                # is unavailable.
                if any(item.is_image for item in attachments) and "vision" not in capabilities:
                    await message.answer(
                        "Provider ini belum mendukung gambar/vision. "
                        "Pilih provider lain dengan /provider."
                    )
                    return

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
                    if item.kind in {"audio", "video"} and provider.attachment_mode(item) != "disabled" and provider.supports_transcription(item):
                        try:
                            transcript = await provider.transcribe_attachment(item)
                        except ProviderError as exc:
                            transcript = None
                            try:
                                await status.edit_text(
                                    f"⚠️ Transkripsi {item.filename or item.kind} gagal: {str(exc)[:180]}"
                                )
                            except Exception:
                                pass
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
            if provider is not None and mapping is not None:
                try:
                    await provider.cleanup_attachments(mapping)
                except Exception:
                    log.debug("Provider attachment cleanup skipped", exc_info=True)
            self.attachments.cleanup(attachments)

def register_handlers(dp: Dispatcher, app: BotApp):
    @router.message.middleware()
    async def register_user_middleware(handler, event, data):
        if event.from_user:
            await app.db.ensure_user(event.from_user.id, username=event.from_user.username, first_name=event.from_user.first_name, last_name=event.from_user.last_name)
        return await handler(event, data)

    @router.message(Command("start"))
    async def start(message: Message):
        await message.answer("TeleChatBot aktif.\nKirim pesan untuk mulai chat.\n/myid — lihat Telegram user ID kamu\n/provider — pilih provider\n/model <nama> — pilih model\n/models — ambil daftar model dari provider\n/baseurl <URL> — custom Base URL\n/protocol <protocol> — custom protocol\n/capabilities <list> — custom capabilities\n/apikey <KEY> — custom API key\n/settings — lihat pengaturan\n/status — lihat konfigurasi aktif\n/clear — hapus riwayat.")

    @router.message(Command("myid"))
    async def myid(message: Message):
        if message.from_user: await message.answer(f"Telegram User ID kamu: {message.from_user.id}")

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer("/start — mulai\n/myid — lihat Telegram user ID kamu\n/provider <nama> — pilih provider preset\n/model <model> — pilih model\n/models — daftar model dari provider\n/baseurl <URL> — set custom Base URL\n/protocol <protocol> — set custom protocol\n/capabilities <list> — set custom capabilities\n/apikey <KEY> — set custom API key\n/settings — lihat pengaturan custom\n/resetsettings — hapus custom Base URL & API key\n/status — status provider\n/clear — hapus riwayat\nKirim teks, foto, PDF, dokumen, audio, atau video.")

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
        protocol = custom["protocol"] or (profile.protocol if profile else "openai_chat_completions")
        capabilities = custom["capabilities"] or (sorted(profile.capabilities) if profile else ["text"])
        await message.answer(
            f"Pengaturan AI kamu:\nProvider: {provider_name}\nProtocol: {protocol}\n"
            f"Base URL: {base_url}\nAPI Key: {mask_api_key(custom['api_key'])}\n"
            f"Model: {model}\nCapabilities: {', '.join(capabilities)}\n\n"
            "/baseurl <URL>\n/protocol <protocol>\n/capabilities <a,b,c>\n"
            "/apikey <KEY>\n/model <MODEL>\n/resetsettings"
        )

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
        protocol = custom["protocol"] or (profile.protocol if profile else "openai_chat_completions")
        capabilities = custom["capabilities"] or (sorted(profile.capabilities) if profile else ["text"])
        await message.answer(f"Provider: {provider_name}\nProtocol: {protocol}\nBase URL: {base_url}\nAPI Key: {'custom' if custom['api_key'] else 'provider/default'}\nModel: {selected}\nCapabilities: {', '.join(capabilities)}\nMode: {app.settings.telegram_mode}")

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

    async def _discover_models(uid):
        provider_name = await app.db.get_provider(uid) or app.settings.ai_default_provider
        profile = app.providers.profile(provider_name)
        custom = await app.db.get_custom_settings(uid)
        provider = app.providers.provider(provider_name, base_url_override=custom["base_url"] or None, api_key_override=custom["api_key"] or None, protocol_override=custom["protocol"] or None, capabilities_override=custom["capabilities"] or None)
        available = await provider.list_models()
        current = await app.db.get_model(uid) or (profile.default_model if profile else "") or app.settings.ai_model
        return provider_name, available, current

    @router.message(Command("models"))
    async def models(message: Message):
        if not message.from_user: return
        try:
            provider_name, info, current = await _discover_model_info(message.from_user.id)
            available = [item.id for item in info]
        except ProviderError as exc: await message.answer(f"Gagal mengambil daftar model: {exc}"); return
        except Exception: log.exception("Model discovery failed"); await message.answer("Gagal mengambil daftar model dari provider."); return
        if not available: await message.answer("Provider " + provider_name + " tidak mengembalikan daftar model otomatis.\nGunakan /model <nama-model> secara manual."); return
        badges = {"vision": "👁", "audio": "🎙", "video": "🎬", "file": "📎", "reasoning": "🧠"}
        detected = []
        for item in info[:20]:
            icons = " ".join(badges[key] for key in badges if key in item.capabilities)
            if icons:
                detected.append(f"• {item.id}  {icons}")
        note = "\n\nKapabilitas hanya ditampilkan jika API provider mengirim metadata eksplisit."
        if detected:
            note += "\n\nDeteksi otomatis:\n" + "\n".join(detected)
        await message.answer(f"🤖 Model tersedia — {provider_name}\nHalaman 1 • {len(available)} model\nTap model untuk mengaktifkan:" + note, reply_markup=model_keyboard(available, current, 0))

    @router.callback_query(F.data.startswith("modelpage:"))
    async def model_page(callback: CallbackQuery):
        if not callback.from_user: return
        try:
            page = int((callback.data or "").split(":", 1)[1])
            provider_name, available, current = await _discover_models(callback.from_user.id)
            if not available: await callback.answer("Tidak ada model yang tersedia.", show_alert=True); return
            page = max(0, min(page, (len(available) - 1) // 8))
            await callback.message.edit_text(f"🤖 Model tersedia — {provider_name}\nHalaman {page + 1} • {len(available)} model\nTap model untuk mengaktifkan:", reply_markup=model_keyboard(available, current, page))
            await callback.answer()
        except ProviderError as exc: await callback.answer(str(exc)[:180], show_alert=True)
        except Exception: log.exception("Model pagination failed"); await callback.answer("Gagal memuat halaman model.", show_alert=True)

    @router.callback_query(F.data.startswith("setmodel:"))
    async def set_model_callback(callback: CallbackQuery):
        if not callback.from_user: return
        raw_index = (callback.data or "").split(":", 1)[1].strip()
        try: index = int(raw_index)
        except ValueError: await callback.answer("Model tidak valid.", show_alert=True); return
        try:
            _, available, _ = await _discover_models(callback.from_user.id)
        except Exception:
            await callback.answer("Gagal memuat model.", show_alert=True); return
        if index < 0 or index >= len(available): await callback.answer("Model sudah tidak tersedia.", show_alert=True); return
        value = available[index]
        await app.db.set_model(callback.from_user.id, value)
        await app.notify_admin(callback.from_user.id, "MODEL", value, None)
        await callback.answer("Model diaktifkan.")
        if callback.message: await callback.message.edit_text(f"✅ Model aktif: {value}")

    @router.message(Command("model"))
    async def model(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = await app.db.get_model(message.from_user.id)
            provider_name = await app.db.get_provider(message.from_user.id) or app.settings.ai_default_provider
            profile = app.providers.profile(provider_name)
            await message.answer(f"Model saat ini: {current or (profile.default_model if profile else '') or app.settings.ai_model or '(default provider)'}\nGunakan /models untuk memilih model.")
            return
        value = parts[1].strip(); await app.db.set_model(message.from_user.id, value); await app.notify_admin(message.from_user.id, "MODEL", value, message); await message.answer(f"Model diubah ke: {value}")
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
        await message.answer(f"Custom Base URL disimpan:\n{value}\n\nEndpoint harus sesuai dengan protocol provider aktif (misalnya Chat Completions atau Responses).")

    @router.message(Command("protocol"))
    async def protocol(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            custom = await app.db.get_custom_settings(message.from_user.id)
            provider_name = await app.db.get_provider(message.from_user.id) or app.settings.ai_default_provider
            profile = app.providers.profile(provider_name)
            await message.answer("Protocol saat ini: " + (custom["protocol"] or (profile.protocol if profile else "openai_chat_completions")) + "\nTersedia: " + ", ".join(sorted(app.providers.SUPPORTED_PROTOCOLS)))
            return
        value = parts[1].strip().lower()
        if value not in app.providers.SUPPORTED_PROTOCOLS:
            await message.answer("Protocol tidak didukung. Tersedia: " + ", ".join(sorted(app.providers.SUPPORTED_PROTOCOLS)))
            return
        await app.db.set_custom_protocol(message.from_user.id, value)
        await app.notify_admin(message.from_user.id, "PROTOCOL", value, message)
        await message.answer("Custom protocol disimpan: " + value)

    @router.message(Command("capabilities"))
    async def capabilities(message: Message):
        if not message.from_user: return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            custom = await app.db.get_custom_settings(message.from_user.id)
            provider_name = await app.db.get_provider(message.from_user.id) or app.settings.ai_default_provider
            profile = app.providers.profile(provider_name)
            current = custom["capabilities"] or (sorted(profile.capabilities) if profile else ["text"])
            await message.answer("Capabilities saat ini: " + ", ".join(current) + "\nTersedia: " + ", ".join(sorted(app.providers.CAPABILITIES)))
            return
        values = [x.strip().lower() for x in parts[1].split(",") if x.strip()]
        unknown = sorted(set(values) - app.providers.CAPABILITIES)
        if unknown or not values:
            await message.answer("Capability tidak valid: " + ", ".join(unknown or ["kosong"]) + "\nTersedia: " + ", ".join(sorted(app.providers.CAPABILITIES)))
            return
        await app.db.set_custom_capabilities(message.from_user.id, values)
        await app.notify_admin(message.from_user.id, "CAPABILITIES", ", ".join(sorted(set(values))), message)
        await message.answer("Custom capabilities disimpan: " + ", ".join(sorted(set(values))))
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
