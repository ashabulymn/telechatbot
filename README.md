# TeleChatBot

Universal Telegram AI chatbot foundation with pluggable AI providers, persistent sessions/history, and attachment handling.

## Features

- Telegram long polling or webhook mode.
- Universal provider profiles via configurable Base URL, API key, model, protocol and capabilities.
- Per-user provider switching with `/provider`.
- Per-user custom Base URL, API key, protocol, and capability overrides.
- Provider adapter boundary with optional native file upload support.
- Per-user conversation history and model selection.
- Stable sequential user and setting numbers for admin setting-change notifications.
- SQLite by default.
- Telegram photos, documents, audio, video, voice and generic files are normalized into an attachment abstraction.
- Environment-based secrets; no credentials in source code.
- Custom per-user API keys are encrypted at rest with Fernet when `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured.
- API keys are not stored in the setting-change audit table, while the admin notification can still receive the full key when configured.
- Docker Compose deployment with health endpoint.
- Separate image size limit to keep multimodal/base64 payloads bounded.
- Telegram shows attachment preparation status while native uploads are running.
- Audio/voice attachments can be transcribed through the provider speech-to-text endpoint before the AI request.
- Video attachments can have their audio extracted with FFmpeg and transcribed, while the original video remains available for native-capable providers.

## Build & setup lengkap

### 1. Persiapan Telegram Bot

Buat bot melalui **@BotFather** dan ambil token bot. Jangan commit token ke Git dan jangan menaruhnya di `.env.example`.

Isi `.env`:

```env
TELEGRAM_BOT_TOKEN=ISI_TOKEN_BOT
TELEGRAM_MODE=polling
```

Untuk mengetahui Telegram user ID admin, gunakan bot seperti biasa lalu masukkan ID tersebut ke:

```env
ADMIN_TELEGRAM_USER_ID=123456789
```

Admin akan menerima notifikasi ketika user mengganti provider, model, Base URL, API key, protocol, capabilities, atau melakukan reset settings.

### 2. Konfigurasi AI

Minimal konfigurasi untuk endpoint OpenAI-compatible:

```env
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=ISI_API_KEY
AI_MODEL=NAMA_MODEL
AI_DEFAULT_PROVIDER=default
```

Untuk endpoint OpenAI-compatible lain, cukup ubah `AI_BASE_URL`, `AI_API_KEY`, dan `AI_MODEL`.

Contoh provider Responses:

```env
AI_PROVIDERS_JSON={"openai":{"protocol":"openai_responses","base_url":"https://api.openai.com/v1","api_key":"$OPENAI_API_KEY","default_model":"gpt-5","capabilities":["text","vision","file","audio","video","transcription","file_cleanup"],"attachment_modes":{"image":"auto","document":"native","pdf":"native","audio":"transcribe","video":"transcribe"}}}
OPENAI_API_KEY=ISI_API_KEY
AI_DEFAULT_PROVIDER=openai
```

Contoh Gemini native:

```env
AI_PROVIDERS_JSON={"gemini":{"protocol":"gemini_generate_content","base_url":"https://generativelanguage.googleapis.com/v1beta","api_key":"$GEMINI_API_KEY","default_model":"gemini-3.8-flash","capabilities":["text","vision"]}}
GEMINI_API_KEY=ISI_API_KEY
AI_DEFAULT_PROVIDER=gemini
```

Gemini juga menyediakan OpenAI compatibility melalui base URL `https://generativelanguage.googleapis.com/v1beta/openai/`; protocol native di atas digunakan untuk adapter Gemini khusus. citeturn0search0turn0search1

Contoh Anthropic:

```env
AI_PROVIDERS_JSON={"anthropic":{"protocol":"anthropic_messages","base_url":"https://api.anthropic.com","api_key":"$ANTHROPIC_API_KEY","default_model":"claude-model","capabilities":["text","vision"]}}
ANTHROPIC_API_KEY=ISI_API_KEY
AI_DEFAULT_PROVIDER=anthropic
```

Protocol yang tersedia saat ini:

| Protocol | Endpoint yang dipakai | Keterangan |
|---|---|---|
| `openai_chat_completions` | `<base_url>/chat/completions` | OpenAI-compatible API |
| `openai_responses` | `<base_url>/responses` | Responses API + native file/transcription bila didukung |
| `anthropic_messages` | `<base_url>/v1/messages` | Anthropic Messages API |
| `gemini_generate_content` | `<base_url>/models/<model>:generateContent` | Gemini native Generate Content API |

> Catatan: kemampuan attachment tidak otomatis berarti provider benar-benar mendukungnya. `capabilities` harus mencerminkan kemampuan endpoint yang digunakan.

### 3. 9Router

TeleChatBot **sudah kompatibel dengan 9Router** melalui protocol `openai_chat_completions`. 9Router menyediakan endpoint OpenAI-compatible, sehingga tidak memerlukan adapter khusus di kode TeleChatBot. Dokumentasi 9Router mencantumkan Base URL cloud `https://9router.com/v1`, self-hosted `http://localhost:20128/v1`, serta model dengan pola seperti `cc/*`, `cx/*`, dan `glm/*`.

Contoh cloud:

```env
NINEROUTER_API_KEY=ISI_API_KEY_9ROUTER
AI_PROVIDERS_JSON={"9router":{"protocol":"openai_chat_completions","base_url":"https://9router.com/v1","api_key":"$NINEROUTER_API_KEY","default_model":"cc/claude-sonnet-4-20250514","capabilities":["text","vision"],"attachment_modes":{"image":"auto","document":"fallback","pdf":"fallback","audio":"fallback","video":"fallback"}}}
AI_DEFAULT_PROVIDER=9router
```

Setelah deploy, user dapat memakai:

```text
/provider 9router
/model cc/claude-sonnet-4-20250514
/status
```

Untuk 9Router yang berjalan dalam Docker network yang sama, gunakan Base URL yang dapat dijangkau container TeleChatBot, misalnya `http://9router:20128/v1` bila nama service/container-nya `9router`. Jangan gunakan `localhost` dari dalam container TeleChatBot karena itu menunjuk ke container TeleChatBot sendiri. 9Router sendiri mendokumentasikan endpoint lokal pada port 20128.

**Catatan attachment:** kompatibilitas OpenAI Chat Completions membuat chat teks dapat langsung dirouting. Kemampuan vision/attachment tetap bergantung pada model/provider yang dipilih di 9Router; jangan mengaktifkan capability yang tidak benar-benar tersedia. Untuk audio/video, konfigurasi di atas memakai `fallback`, bukan mengklaim endpoint transcription native 9Router sebagai bagian dari adapter Chat Completions TeleChatBot.

### 4. Enkripsi API key custom user

Generate Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Masukkan hasilnya:

```env
CUSTOM_SETTINGS_ENCRYPTION_KEY=HASIL_FERNET
```

**Simpan key ini dengan aman.** Jika hilang, data API key custom yang terenkripsi tidak dapat didekripsi.

### 4. Clone dan build

```bash
git clone https://github.com/ashabulymn/telechatbot.git
cd telechatbot
cp .env.example .env
```

Edit `.env`, lalu build:

```bash
docker compose up -d --build
```

Cek container:

```bash
docker compose ps
docker compose logs -f telechatbot
```

Health endpoint:

```text
http://SERVER_IP:8080/health
```

Stop/rebuild:

```bash
docker compose down
docker compose up -d --build
```

Data database dan attachment tersimpan di Docker volume `telechatbot_data`, sehingga tidak hilang ketika container dibuat ulang.

### 5. Setting bot setelah build

Kirim ke bot:

```text
/start
/help
/settings
/status
```

Per-user setting:

```text
/provider
/provider NAMA_PROVIDER
/model NAMA_MODEL
/baseurl https://example.com/v1
/protocol openai_chat_completions
/capabilities text,vision
/apikey API_KEY
/resetsettings
```

Contoh custom endpoint:

```text
/baseurl https://example.com/v1
/protocol openai_chat_completions
/capabilities text,vision
/model model-name
/apikey sk-xxxxxxxx
```

Setelah memakai `/apikey`, hapus pesan Telegram yang berisi API key jika ingin mengurangi jejak credential di chat.

### 6. Deploy dengan Docker Compose / Dokploy

Repository sudah menyediakan `Dockerfile` dan `docker-compose.yml`.

Di server:

1. Buat project/application dari repository GitHub.
2. Gunakan Docker Compose.
3. Pastikan file `.env`/environment variables diisi dari konfigurasi server, bukan di-commit ke repository.
4. Expose port container `8080` hanya jika diperlukan.
5. Persistent volume harus dipertahankan untuk `/app/data`.
6. Deploy/redeploy setelah environment variable diubah.

Untuk mode polling, tidak diperlukan domain publik atau webhook. Bot langsung melakukan long polling ke Telegram.

Untuk mode webhook:

```env
TELEGRAM_MODE=webhook
TELEGRAM_WEBHOOK_URL=https://bot.example.com
TELEGRAM_WEBHOOK_SECRET=SECRET_RANDOM
TELEGRAM_WEBHOOK_HOST=0.0.0.0
TELEGRAM_WEBHOOK_PORT=8080
```

Domain harus mengarah ke server dan reverse proxy harus meneruskan request ke port `8080`. Aplikasi otomatis memakai endpoint `/telegram/webhook`; jika nilai `TELEGRAM_WEBHOOK_URL` sudah berisi path tersebut, tidak akan ditambahkan lagi.

### 7. Attachment & FFmpeg

Container sudah memasang FFmpeg untuk ekstraksi audio dari video.

Default:

```env
ATTACHMENT_MAX_MB=20
ATTACHMENT_MAX_IMAGE_MB=5
ATTACHMENT_MAX_PROMPT_CHARS=30000
AI_VIDEO_TRANSCRIPTION_ENABLED=true
AI_TRANSCRIPTION_MAX_SECONDS=300
```

Audio/video transcription hanya berjalan bila protocol/provider menyatakan capability `transcription` dan mode attachment mengizinkannya.

### 8. Keamanan production

- Jangan commit `.env`.
- Jangan commit Telegram bot token atau API key.
- Gunakan `CUSTOM_SETTINGS_ENCRYPTION_KEY` yang persistent.
- Jangan menghapus Docker volume `telechatbot_data` saat redeploy.
- API key user dikirim melalui Telegram ketika memakai `/apikey`; hapus pesan tersebut setelah selesai.
- Admin notification dapat berisi API key penuh sesuai konfigurasi aplikasi. Pastikan akun Telegram admin aman.
- Jika bot token pernah terekspos, revoke/regenerate token melalui BotFather sebelum deployment production.

## Quick start

1. Copy `.env.example` to `.env`.
2. Set `TELEGRAM_BOT_TOKEN`, `AI_API_KEY`, `AI_BASE_URL`, and `AI_MODEL`.
3. For per-user custom API keys, set a persistent `CUSTOM_SETTINGS_ENCRYPTION_KEY`. Generate one with:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. Run `docker compose up -d --build`.
5. Send `/start` to the bot.

For webhook mode, set `TELEGRAM_MODE=webhook` and provide a public HTTPS `TELEGRAM_WEBHOOK_URL`.

Attachment support is provider-dependent. The generic Chat Completions protocol uses image data URLs and bounded document extraction. The `openai_responses` protocol adds native `/files` upload and `input_file` mapping for providers implementing the OpenAI Responses API.

## Multiple providers

Set `AI_PROVIDERS_JSON` to define named OpenAI-compatible endpoints. Example:

```json
{"openrouter":{"base_url":"https://openrouter.ai/api/v1","api_key":"$OPENROUTER_API_KEY","default_model":"your-model","capabilities":["text","vision"],"attachment_modes":{"image":"auto","document":"fallback","pdf":"fallback","audio":"transcribe","video":"transcribe"}},"custom":{"base_url":"https://example.com/v1","api_key":"your-key","default_model":"your-model","capabilities":["text"],"attachment_modes":{"image":"disabled"}}}
```

The selected provider is stored per Telegram user. `/provider` lists providers and `/provider NAME` switches the active one. Provider profiles can also declare `attachment_modes` per file kind: `auto`, `native`, `transcribe`, `fallback`, or `disabled`, sehingga provider/router dapat menentukan cara menangani setiap lampiran tanpa mengubah layer Telegram. `disabled` is enforced before processing; `native` prefers native upload; `transcribe` prefers speech-to-text for audio/video; `fallback` skips native upload and uses the generic mapper.

## Per-user custom endpoint

Each Telegram user can override the active provider without changing server environment variables:

- `/settings` — show the active settings (API key is masked).
- `/baseurl https://example.com/v1` — set a custom Base URL.
- `/protocol openai_responses` — override the protocol used by the custom endpoint. Supported: `openai_chat_completions`, `openai_responses`, `anthropic_messages`, `gemini_generate_content`.
- `/capabilities text,vision,file,transcription` — declare capabilities of the custom endpoint. This controls attachment behavior and prevents the bot from assuming the preset provider's capabilities.
- `/apikey YOUR_KEY` — set a custom API key. The key is encrypted at rest when `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured and is never displayed in full to the user.
- `/model MODEL_NAME` — select the model for the current user.\n- `/models` — query the active provider for its live model list when the provider exposes model discovery. OpenAI-compatible providers use `/models`; the Gemini native adapter uses Gemini's `models.list`; Anthropic uses its `/v1/models` endpoint.
- `/resetsettings` — remove custom Base URL, API key, protocol, and capabilities while keeping provider/model selection.

Existing legacy plaintext custom API keys remain readable for migration; once read while `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured, they are automatically re-encrypted. New custom API keys require the encryption key.

A custom endpoint can override the preset provider protocol and capabilities per user. For example, `openai_chat_completions` uses `<base_url>/chat/completions`, while `openai_responses` uses `<base_url>/responses` and may also use the provider's file/transcription endpoints when the declared capabilities enable them. Because an API key sent through Telegram appears in the Telegram chat history, delete the `/apikey ...` message after setting it if that matters for your threat model.

## Admin setting notifications

Set `ADMIN_TELEGRAM_USER_ID` to receive notifications when users change provider, model, Base URL, API key, or reset custom settings.

Notifications include stable `User #` and `Setting #` numbers. For the API key event, the admin notification intentionally contains the full API key as configured, while the audit database stores only `[API KEY TIDAK DISIMPAN DI AUDIT]`.
