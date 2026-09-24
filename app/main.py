import asyncio,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI,Header,HTTPException,Request
from aiogram import Bot,Dispatcher
from .bot import BotApp,register_handlers
from .config import get_settings
from .db import Database
s=get_settings(); logging.basicConfig(level=getattr(logging,s.log_level.upper(),logging.INFO)); bot=Bot(s.telegram_bot_token); dp=Dispatcher(); db=Database(); logic=BotApp(bot,db)
@asynccontextmanager
async def lifespan(app:FastAPI):
    await db.init()
    if s.telegram_mode.lower()=="webhook":
        if not s.telegram_webhook_url: raise RuntimeError("TELEGRAM_WEBHOOK_URL is required in webhook mode")
        if not s.telegram_webhook_secret:
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required in webhook mode")
        webhook_url = s.telegram_webhook_url.rstrip("/")
        if not webhook_url.endswith("/telegram/webhook"):
            webhook_url += "/telegram/webhook"
        await bot.set_webhook(webhook_url,secret_token=s.telegram_webhook_secret)
    yield
    if s.telegram_mode.lower()=="webhook": await bot.delete_webhook()
    await bot.session.close()
api=FastAPI(title="TeleChatBot",version="0.1.0",lifespan=lifespan); register_handlers(dp,logic)
@api.get("/health")
async def health(): return {"status":"ok","mode":s.telegram_mode}
@api.post("/telegram/webhook")
async def webhook(request:Request,x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    if s.telegram_mode.lower()!="webhook": raise HTTPException(status_code=404,detail="Webhook mode is disabled")
    if s.telegram_webhook_secret and x_telegram_bot_api_secret_token!=s.telegram_webhook_secret: raise HTTPException(status_code=401,detail="Invalid webhook secret")
    from aiogram.types import Update
    await dp.feed_update(bot,Update.model_validate(await request.json())); return {"ok":True}
async def run():
    if s.telegram_mode.lower()=="polling": await db.init(); await bot.delete_webhook(drop_pending_updates=False); await dp.start_polling(bot); return
    import uvicorn
    await uvicorn.Server(uvicorn.Config(api,host=s.telegram_webhook_host,port=s.telegram_webhook_port,log_level=s.log_level.lower())).serve()
