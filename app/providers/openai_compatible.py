import httpx
from .base import AIProvider,ProviderResponse
from ..config import get_settings
class OpenAICompatibleProvider(AIProvider):
    def __init__(self): self.settings=get_settings()
    async def chat(self,messages,model=None):
        if not self.settings.ai_api_key: raise RuntimeError("AI_API_KEY is not configured")
        selected=model or self.settings.ai_model
        if not selected: raise RuntimeError("AI_MODEL is not configured")
        url=self.settings.ai_base_url.rstrip("/")+"/chat/completions"
        headers={"Authorization":f"Bearer {self.settings.ai_api_key}","Content-Type":"application/json"}
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            r=await client.post(url,headers=headers,json={"model":selected,"messages":messages}); r.raise_for_status(); data=r.json()
        content=(data.get("choices") or [{}])[0].get("message",{}).get("content","")
        if isinstance(content,list): content="".join(p.get("text","") for p in content if isinstance(p,dict) and p.get("type")=="text")
        return ProviderResponse(str(content),data)
