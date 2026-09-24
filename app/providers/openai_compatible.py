import httpx
from .base import AIProvider, ProviderResponse
from .errors import ProviderConfigurationError, ProviderError
from .registry_types import ProviderRuntime

class OpenAICompatibleProvider(AIProvider):
    def __init__(self, profile=None):
        from ..config import get_settings
        self.settings = get_settings()
        self.runtime = profile or ProviderRuntime(
            name="default", base_url=self.settings.ai_base_url,
            api_key=self.settings.ai_api_key, default_model=self.settings.ai_model,
            extra_body={}
        )

    async def chat(self, messages, model=None):
        if not self.runtime.api_key:
            raise ProviderConfigurationError("API key provider belum dikonfigurasi.")
        selected = model or self.runtime.default_model
        if not selected:
            raise ProviderConfigurationError("Model provider belum dikonfigurasi.")

        url = self.runtime.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.runtime.api_key}", "Content-Type": "application/json"}
        payload = {"model": selected, "messages": messages}
        payload.update(self.runtime.extra_body)

        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError("Provider AI timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider AI tidak dapat dihubungi.") from exc

        if response.is_error:
            detail=""
            try:
                data=response.json()
                err=data.get("error",{})
                detail=str(err.get("message",""))[:300] if isinstance(err,dict) else ""
            except Exception:
                pass
            raise ProviderError(f"Provider AI HTTP {response.status_code}" + (f": {detail}" if detail else "."))

        try:
            data=response.json()
        except ValueError as exc:
            raise ProviderError("Provider AI mengembalikan JSON yang tidak valid.") from exc
        choices=data.get("choices") or []
        if not choices:
            raise ProviderError("Provider AI tidak mengembalikan choices.")
        content=(choices[0].get("message") or {}).get("content","")
        if isinstance(content,list):
            content="".join(part.get("text","") for part in content if isinstance(part,dict) and part.get("type")=="text")
        return ProviderResponse(str(content),data)
