import json
import httpx
from .base import AIProvider, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from ..config import get_settings

class OpenAICompatibleProvider(AIProvider):
    def __init__(self):
        self.settings = get_settings()

    async def chat(self, messages, model=None):
        if not self.settings.ai_api_key:
            raise ProviderConfigurationError("AI_API_KEY belum dikonfigurasi.")
        selected = model or self.settings.ai_model
        if not selected:
            raise ProviderConfigurationError("AI_MODEL belum dikonfigurasi.")

        url = self.settings.ai_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": selected, "messages": messages}
        if self.settings.ai_extra_body_json:
            try:
                extra = json.loads(self.settings.ai_extra_body_json)
                if not isinstance(extra, dict):
                    raise ValueError
                payload.update(extra)
            except ValueError:
                raise ProviderConfigurationError("AI_EXTRA_BODY_JSON bukan JSON object yang valid.")

        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            raise ProviderError("Provider AI timeout.")
        except httpx.HTTPError:
            raise ProviderError("Provider AI tidak dapat dihubungi.")

        if response.is_error:
            detail = ""
            try:
                data = response.json()
                detail = str(data.get("error", {}).get("message", ""))[:300]
            except Exception:
                pass
            raise ProviderError(f"Provider AI HTTP {response.status_code}" + (f": {detail}" if detail else "."))

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Provider AI tidak mengembalikan choices.")
        content = (choices[0].get("message") or {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ProviderResponse(str(content), data)
