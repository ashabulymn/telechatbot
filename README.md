# TeleChatBot

Universal Telegram AI chatbot foundation with pluggable AI providers, persistent sessions/history, and attachment handling.

## Features

- Telegram long polling or webhook mode.
- Universal provider profiles via configurable Base URL, API key, model, protocol and capabilities.
- Per-user provider switching with `/provider`.
- Per-user custom Base URL and API key overrides with `/baseurl` and `/apikey`.
- Provider adapter boundary with optional native file upload support.
- Per-user conversation history and model selection.
- Stable sequential user and setting numbers for admin setting-change notifications.
- SQLite by default.
- Telegram photos, documents, audio, video, voice and generic files are normalized into an attachment abstraction.
- Environment-based secrets; no credentials in source code.
- Custom per-user API keys are encrypted at rest with Fernet when `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured.
- API keys are not stored in the setting-change audit table, while the admin notification can still receive the full key when configured.
- Docker Compose deployment with health endpoint.\n- Separate image size limit to keep multimodal/base64 payloads bounded.\n- Telegram shows attachment preparation status while native uploads are running.\n- Audio/voice attachments can be transcribed through the provider speech-to-text endpoint before the AI request.\n- Video attachments can have their audio extracted with FFmpeg and transcribed, while the original video remains available for native-capable providers.

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
{"openrouter":{"base_url":"https://openrouter.ai/api/v1","api_key":"$OPENROUTER_API_KEY","default_model":"your-model","capabilities":["text","vision"]},"custom":{"base_url":"https://example.com/v1","api_key":"your-key","default_model":"your-model","capabilities":["text"]}}
```

The selected provider is stored per Telegram user. `/provider` lists providers and `/provider NAME` switches the active one. Native provider implementations can be added later without changing the Telegram layer.

## Per-user custom endpoint

Each Telegram user can override the active provider without changing server environment variables:

- `/settings` — show the active settings (API key is masked).
- `/baseurl https://example.com/v1` — set a custom OpenAI-compatible Base URL.
- `/apikey YOUR_KEY` — set a custom API key. The key is encrypted at rest when `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured and is never displayed in full to the user.
- `/model MODEL_NAME` — select the model for the current user.
- `/resetsettings` — remove the custom Base URL and API key while keeping provider/model selection.

Existing legacy plaintext custom API keys remain readable for migration; once read while `CUSTOM_SETTINGS_ENCRYPTION_KEY` is configured, they are automatically re-encrypted. New custom API keys require the encryption key.

A custom endpoint currently needs to implement OpenAI Chat Completions at `<base_url>/chat/completions`. Because an API key sent through Telegram appears in the Telegram chat history, delete the `/apikey ...` message after setting it if that matters for your threat model.

## Admin setting notifications

Set `ADMIN_TELEGRAM_USER_ID` to receive notifications when users change provider, model, Base URL, API key, or reset custom settings.

Notifications include stable `User #` and `Setting #` numbers. For the API key event, the admin notification intentionally contains the full API key as configured, while the audit database stores only `[API KEY TIDAK DISIMPAN DI AUDIT]`.
