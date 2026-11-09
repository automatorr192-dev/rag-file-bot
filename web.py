import os
import secrets
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, Update
from fastapi import FastAPI, HTTPException, Request

import db
from bot import DESCRIPTION, SHORT_DESCRIPTION, router
from models import chat_client, embed


@asynccontextmanager
async def lifespan(app):
    engine = db.make_engine()
    try:
        secret = os.environ["WEBHOOK_SECRET"]
        public_url = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        await embed(["проверка готовности"])
        async with Bot(os.environ["BOT_TOKEN"]) as bot, chat_client() as client:
            app.state.engine = engine
            app.state.bot = bot
            app.state.client = client
            app.state.dispatcher = dispatcher
            app.state.secret = secret
            await bot.set_my_description(DESCRIPTION)
            await bot.set_my_short_description(SHORT_DESCRIPTION)
            await bot.set_my_commands([BotCommand(command="start", description="Открыть меню")])
            await bot.set_webhook(
                f"{public_url}/telegram",
                secret_token=secret,
                allowed_updates=dispatcher.resolve_used_update_types(),
                max_connections=4,
            )
            yield
    finally:
        await engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    async with app.state.engine.connect() as conn:
        await db.query(conn, "SELECT 1")
    return {"status": "ok"}


@app.post("/telegram")
async def telegram(request: Request):
    state = request.app.state
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(received.encode(), state.secret.encode()):
        raise HTTPException(status_code=403)
    update = Update.model_validate_json(await request.body(), context={"bot": state.bot})
    await state.dispatcher.feed_update(state.bot, update, engine=state.engine, client=state.client)
    return {"ok": True}
