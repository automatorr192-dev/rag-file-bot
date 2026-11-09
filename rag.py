import asyncio
import hashlib
import io
import json
import os

import structlog
from openai import APIError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

import db
from models import DEFAULT_CHAT_MODEL, embed
from prompts import ANSWER, REWRITE

log = structlog.get_logger()
MAX_BYTES = 5 * 1024 * 1024
NO_ANSWER = "В выбранных документах не нашёл ответа на этот вопрос."


class FileRejected(ValueError):
    pass


def split_text(body):
    remaining = " ".join(body.split())
    while remaining:
        if len(remaining) <= 1600:
            yield remaining
            break
        cut = remaining.rfind(" ", 1200, 1600)
        if cut == -1:
            cut = 1600
        yield remaining[:cut]
        remaining = remaining[cut - 160 :].lstrip()


def extract(name, content):
    if len(content) > MAX_BYTES:
        raise FileRejected("Файл больше 5 МБ.")
    try:
        if name.lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise FileRejected("PDF защищён паролем.")
            if len(reader.pages) > 50:
                raise FileRejected("В PDF больше 50 страниц.")
            chunks = []
            for page, source in enumerate(reader.pages, 1):
                chunks.extend((f"стр. {page}", part) for part in split_text(source.extract_text()))
                if len(chunks) > 300:
                    raise FileRejected("В файле слишком много текста.")
        elif name.lower().endswith(".txt"):
            chunks = []
            for index, part in enumerate(split_text(content.decode("utf-8-sig")), 1):
                if index > 300:
                    raise FileRejected("В файле слишком много текста.")
                chunks.append((f"фрагмент {index}", part))
        else:
            raise FileRejected("Загрузи PDF или TXT.")
    except (PyPdfError, UnicodeError, RecursionError) as exc:
        raise FileRejected("Файл не читается. Для TXT нужна кодировка UTF-8.") from exc
    if not chunks:
        raise FileRejected("В файле нет текста. Для PDF нужен текстовый слой.")
    return chunks


async def ingest(engine, owner, name, content):
    digest = hashlib.sha256(content).hexdigest()
    async with engine.begin() as conn:
        await db.query(conn, "SELECT pg_advisory_xact_lock(CAST(:owner AS bigint))", owner=owner)
        rows = await db.query(
            conn, "SELECT digest FROM rag.documents WHERE owner=:owner", owner=owner
        )
        digests = rows.scalars().all()
        if digest in digests:
            return False
        if owner and len(digests) >= 5:
            raise FileRejected("Можно хранить до пяти файлов. Удали один в разделе «Мои файлы».")
        chunks = await asyncio.to_thread(extract, name, content)
        vectors = await embed([body for _, body in chunks])
        row = await db.query(
            conn,
            """INSERT INTO rag.documents(owner,name,digest)
            VALUES (:owner,:name,:digest) RETURNING id""",
            owner=owner,
            name=name,
            digest=digest,
        )
        document_id = row.scalar_one()
        for (location, body), vector in zip(chunks, vectors, strict=True):
            await db.query(
                conn,
                """INSERT INTO rag.chunks(document_id,location,body,embedding)
                VALUES (:id,:location,:body,CAST(CAST(:vector AS text) AS vector))""",
                id=document_id,
                location=location,
                body=body,
                vector=json.dumps(vector),
            )
    return True


async def complete(client, prompt, payload, *, json_mode=False):
    response = await client.chat.completions.create(
        model=os.getenv("CHAT_MODEL") or DEFAULT_CHAT_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_completion_tokens=800 if json_mode else 250,
        extra_body={"reasoning": {"enabled": False}},
    )
    return response.choices[0].message.content or ""


def validate_response(raw, hits):
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        result = json.loads(raw)
    except ValueError:
        return NO_ANSWER, []
    if not isinstance(result, dict):
        return NO_ANSWER, []
    answer, citations = result.get("answer"), result.get("citations")
    allowed = {row["id"]: row for row in hits}
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or len(answer) > 1500
        or not isinstance(citations, list)
        or not citations
        or any(type(cid) is not int or cid not in allowed for cid in citations)
    ):
        return NO_ANSWER, []
    return answer.strip(), [allowed[cid] for cid in dict.fromkeys(citations)]


async def answer_question(engine, client, owner, mode, question):
    async with engine.connect() as conn:
        history = await db.history(conn, owner, mode)
    search_query = question
    model_available = True
    if history:
        try:
            search_query = (
                await complete(client, REWRITE, {"history": history, "question": question})
            ).strip()[:1500] or question
        except APIError as exc:
            log.warning("model_unavailable", error_type=type(exc).__name__)
            model_available = False
            search_query = f"{question} {history[-1]['question']}"[:1500]
    (vector,) = await embed([search_query])
    started = asyncio.get_running_loop().time()
    async with engine.connect() as conn:
        hits = await db.search(conn, 0 if mode == "demo" else owner, search_query, vector)
    log.info(
        "retrieval",
        mode=mode,
        hits=len(hits),
        duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
    )
    if not hits:
        return NO_ANSWER, []
    if model_available:
        try:
            raw = await complete(
                client, ANSWER, {"question": search_query, "sources": hits}, json_mode=True
            )
            return validate_response(raw, hits)
        except APIError as exc:
            log.warning("model_unavailable", error_type=type(exc).__name__)
    return (
        "Модель временно недоступна. Нажми «Источники», чтобы прочитать найденные фрагменты.",
        hits[:3],
    )
