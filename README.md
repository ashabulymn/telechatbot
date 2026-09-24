# TeleChatBot

Universal Telegram AI chatbot foundation with pluggable AI providers, persistent sessions/history, and attachment handling.

## Features

- Telegram long polling or webhook mode.
- Universal provider profiles via configurable Base URL, API key, model, protocol and capabilities.
- Per-user provider switching with `/provider`.
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

## Multiple providers

Set `AI_PROVIDERS_JSON` to define named OpenAI-compatible endpoints. Example:

```json
{"openrouter":{"base_url":"https://openrouter.ai/api/v1","api_key":"$OPENROUTER_API_KEY","default_model":"your-model","capabilities":["text","vision"]},"custom":{"base_url":"https://example.com/v1","api_key":"your-key","default_model":"your-model","capabilities":["text"]}}
```

The selected provider is stored per Telegram user. `/provider` lists providers and `/provider NAME` switches the active one. Native provider implementations can be added later without changing the Telegram layer.
