# TeleChatBot

Universal Telegram AI chatbot foundation with pluggable AI providers, persistent sessions/history, and attachment handling.

## Features

- Telegram long polling or webhook mode.
- OpenAI-compatible provider via configurable Base URL, API key, and model.
- Provider adapter boundary for future native providers.
- Per-user conversation history and model selection.
- SQLite by default.
- Telegram photos, documents, audio, video, voice and generic files are normalized into an attachment abstraction.
- Environment-based secrets; no credentials in source code.
- Docker Compose deployment with health endpoint.

## Quick start

1. Copy .env.example to .env.
2. Set TELEGRAM_BOT_TOKEN, AI_API_KEY, AI_BASE_URL, and AI_MODEL.
3. Run docker compose up -d --build.
4. Send /start to the bot.

For webhook mode, set TELEGRAM_MODE=webhook and provide a public HTTPS TELEGRAM_WEBHOOK_URL.

Attachment support is provider-dependent. Images can be passed as data URLs to providers that accept OpenAI-style multimodal messages. Other files are preserved through the attachment abstraction for provider-specific adapters.
