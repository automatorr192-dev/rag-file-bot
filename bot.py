import asyncio
import io
import json
import logging
import os

import sentry_sdk
import structlog
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

import db
from models import chat_client, embed
from rag import MAX_BYTES, FileRejected, answer_question, ingest

DESCRIPTION = "Задай вопрос по базе магазина или загрузи свой PDF/TXT. Ответы с источниками."
SHORT_DESCRIPTION = "Поиск и ответы по PDF и TXT с источниками и памятью диалога."
WELCOME = "Попробуй демо: спроси, в какой срок можно вернуть товар. Или открой свои файлы."
NOTICE = (
    "Текст загруженных файлов обрабатывается внешним API. "
    "Используй публичные или тестовые документы. Поддерживаются PDF с текстом и TXT."
)
log = structlog.get_logger()
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class SerialRequests(BaseMiddleware):
    def __init__(self):
        self.active = set()

    async def __call__(self, handler, event, data):
        owner = event.from_user.id
        if owner in self.active:
            await event.answer("Дождись завершения текущего запроса.")
            return
        self.active.add(owner)
        try:
            await handler(event, data)
        except Exception as exc:
            log.warning("request_failed", error_type=type(exc).__name__)
            sentry_sdk.capture_message(f"Bot request failed: {type(exc).__name__}", level="warning")
            await event.answer("Не удалось выполнить запрос. Попробуй ещё раз.")
        finally:
            self.active.remove(owner)


serial = SerialRequests()
router.message.middleware(serial)
router.callback_query.middleware(serial)


def keyboard(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
            for row in rows
        ]
    )


MENU = keyboard(
    [("Попробовать демо", "demo"), ("Мои файлы", "files")],
    [("Новый диалог", "reset")],
)


@router.message(CommandStart())
async def start(message):
    await message.answer(WELCOME, reply_markup=MENU)


@router.callback_query(F.data.in_({"demo", "files", "allow", "reset"}))
async def menu(callback, engine):
    await callback.answer()
    owner = callback.from_user.id
    action = callback.data
    markup = MENU
    async with engine.begin() as conn:
        state = await db.user(conn, owner)
        if action == "reset":
            await db.clear(conn, owner, state["mode"])
            reply = "Контекст текущего диалога очищен."
        elif action == "demo":
            await db.query(conn, "UPDATE rag.users SET mode='demo' WHERE owner=:owner", owner=owner)
            reply = "Демо магазина «Северный Лис». Спроси про возврат, доставку или лампу LX-410."
        else:
            await db.query(
                conn, "UPDATE rag.users SET mode='files' WHERE owner=:owner", owner=owner
            )
            if action == "allow":
                await db.query(
                    conn, "UPDATE rag.users SET upload_allowed=true WHERE owner=:owner", owner=owner
                )
            rows = await db.query(
                conn,
                "SELECT id,name FROM rag.documents WHERE owner=:owner ORDER BY id",
                owner=owner,
            )
            buttons = [[(f"Удалить {name[:40]}", f"confirm:{key}")] for key, name in rows]
            if not state["upload_allowed"] and action != "allow":
                buttons.append([("Понятно, разрешить загрузку", "allow")])
                reply = NOTICE
            else:
                reply = "Отправь PDF или TXT. Файлы ниже можно удалить."
            markup = keyboard(
                *buttons,
                *[
                    [("Попробовать демо", "demo"), ("Новый диалог", "reset")],
                ],
            )
    await callback.message.answer(reply, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^confirm:[0-9]{1,18}$"))
async def confirm_delete(callback):
    await callback.answer()
    key = callback.data.split(":")[1]
    await callback.message.answer(
        "Удалить файл и историю режима «Мои файлы»?",
        reply_markup=keyboard([("Удалить", f"delete:{key}"), ("Отмена", "files")]),
    )


@router.callback_query(F.data.regexp(r"^delete:[0-9]{1,18}$"))
async def delete(callback, engine):
    await callback.answer()
    async with engine.begin() as conn:
        deleted = await db.delete_document(
            conn, callback.from_user.id, int(callback.data.split(":")[1])
        )
    reply = "Файл и история удалены." if deleted else "Файл уже недоступен."
    await callback.message.answer(reply, reply_markup=MENU)


@router.callback_query(F.data.regexp(r"^sources:[0-9]{1,18}$"))
async def sources(callback, engine):
    await callback.answer()
    async with engine.connect() as conn:
        row = await db.query(
            conn,
            "SELECT sources FROM rag.turns WHERE owner=:owner AND id=:id",
            owner=callback.from_user.id,
            id=int(callback.data.split(":")[1]),
        )
        data = row.scalar_one_or_none()
    if data is None:
        await callback.message.answer("Источники этого ответа больше недоступны.")
        return
    for hit in data:
        await callback.message.answer(f"{hit['name']} · {hit['location']}\n\n{hit['body']}")


@router.message(F.document)
async def upload(message, bot, engine):
    owner = message.from_user.id
    async with engine.begin() as conn:
        state = await db.user(conn, owner)
    if not state["upload_allowed"]:
        await message.answer(
            NOTICE, reply_markup=keyboard([("Понятно, разрешить загрузку", "allow")])
        )
        return
    doc = message.document
    name = (doc.file_name or "Файл")[:200]
    if not name.lower().endswith((".pdf", ".txt")):
        await message.answer("Загрузи PDF или TXT.")
        return
    if doc.file_size is None or doc.file_size > MAX_BYTES:
        await message.answer("Нужен файл размером до 5 МБ.")
        return
    await message.answer("Читаю и индексирую файл…")
    target = io.BytesIO()
    await bot.download(doc, destination=target, timeout=20)
    try:
        created = await ingest(engine, owner, name, target.getvalue())
    except FileRejected as exc:
        await message.answer(str(exc))
        return
    async with engine.begin() as conn:
        await db.query(conn, "UPDATE rag.users SET mode='files' WHERE owner=:owner", owner=owner)
    reply = "Файл готов. Задай вопрос по своим документам." if created else "Файл уже загружен."
    await message.answer(reply, reply_markup=MENU)


@router.message(F.text & ~F.text.startswith("/"))
async def question(message, engine, client):
    owner = message.from_user.id
    text = message.text.strip()
    if not text or len(text) > 1500:
        await message.answer("Задай текстовый вопрос до 1500 символов.")
        return
    async with engine.begin() as conn:
        state = await db.user(conn, owner)
        mode = state["mode"]
        if mode == "demo" and not await db.charge_demo(conn, owner):
            await message.answer("Достигнут лимит демо: 20 вопросов в сутки.")
            return
    await message.bot.send_chat_action(message.chat.id, "typing")
    async with asyncio.timeout(24):
        answer, hits = await answer_question(engine, client, owner, mode, text)
    async with engine.begin() as conn:
        row = await db.query(
            conn,
            """INSERT INTO rag.turns(owner,mode,question,answer,sources)
            VALUES (:owner,:mode,:question,:answer,
                CAST(CAST(:sources AS text) AS jsonb)) RETURNING id""",
            owner=owner,
            mode=mode,
            question=text,
            answer=answer,
            sources=json.dumps(hits),
        )
        key = row.scalar_one()
        await db.query(
            conn,
            """DELETE FROM rag.turns WHERE owner=:owner AND mode=:mode AND id NOT IN (
                SELECT id FROM rag.turns WHERE owner=:owner AND mode=:mode
                ORDER BY id DESC LIMIT 6
            )""",
            owner=owner,
            mode=mode,
        )
    if hits:
        answer += "\n\n" + "\n".join(f"[{hit['name']} · {hit['location']}]" for hit in hits)
        markup = keyboard(
            [("Источники", f"sources:{key}")],
            *[
                [("Мои файлы", "files"), ("Новый диалог", "reset")],
            ],
        )
    else:
        markup = MENU
    await message.answer(answer, reply_markup=markup)


async def main():
    engine = db.make_engine()
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    if dsn := os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=dsn,
            default_integrations=False,
            send_default_pii=False,
            include_local_variables=False,
            max_breadcrumbs=0,
        )
    try:
        await embed(["проверка готовности"])
        async with (
            Bot(os.environ["BOT_TOKEN"]) as bot,
            chat_client() as client,
        ):
            await bot.set_my_description(DESCRIPTION)
            await bot.set_my_short_description(SHORT_DESCRIPTION)
            await bot.set_my_commands([BotCommand(command="start", description="Открыть меню")])
            log.info("polling_ready")
            dispatcher = Dispatcher()
            dispatcher.include_router(router)
            await dispatcher.start_polling(bot, engine=engine, client=client)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
