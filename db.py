import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

SEARCH = text(Path(__file__).with_name("search.sql").read_text(encoding="utf-8"))


class LocalConnection(asyncpg.Connection):
    async def close(self, *, timeout=None):  # noqa: ASYNC109
        try:
            await super().close(timeout=timeout if timeout is not None else 1)
        except TimeoutError:
            self.terminate()


def make_engine():
    load_dotenv(Path(__file__).with_name(".env"))
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url.removeprefix(prefix)
            break
    options = {"server_settings": {"search_path": "public,extensions"}}
    local = os.getenv("PGLITE_LOCAL") == "1"
    if local:
        options.update(
            connection_class=LocalConnection,
            prepared_statement_name_func=lambda: f"rag_{uuid4().hex}",
            statement_cache_size=0,
            timeout=8,
            command_timeout=8,
        )
    return create_async_engine(
        url,
        pool_pre_ping=not local,
        hide_parameters=True,
        connect_args=options,
    )


async def query(connection, sql, **params):
    return await connection.execute(text(sql), params)


async def search(connection, owner, question, vector, strategy="hybrid"):
    rows = await connection.execute(
        SEARCH,
        dict(owner=owner, question=question, embedding=json.dumps(vector), strategy=strategy),
    )
    return [dict(row) for row in rows.mappings()]


async def user(connection, owner):
    await query(
        connection,
        "INSERT INTO rag.users(owner) VALUES (:owner) ON CONFLICT DO NOTHING",
        owner=owner,
    )
    result = await query(connection, "SELECT * FROM rag.users WHERE owner=:owner", owner=owner)
    return result.mappings().one()


async def history(connection, owner, mode):
    rows = await query(
        connection,
        """SELECT question, answer FROM rag.turns
        WHERE owner=:owner AND mode=:mode ORDER BY id DESC LIMIT 6""",
        owner=owner,
        mode=mode,
    )
    return [dict(row) for row in reversed(rows.mappings().all())]


async def clear(connection, owner, mode):
    await query(
        connection,
        "DELETE FROM rag.turns WHERE owner=:owner AND mode=:mode",
        owner=owner,
        mode=mode,
    )


async def delete_document(connection, owner, document_id):
    result = await query(
        connection,
        "DELETE FROM rag.documents WHERE owner=:owner AND id=:id RETURNING id",
        owner=owner,
        id=document_id,
    )
    if result.scalar_one_or_none() is None:
        return False
    await clear(connection, owner, "files")
    return True


async def charge_demo(connection, owner):
    result = await query(
        connection,
        """INSERT INTO rag.quota(owner, day, used)
        VALUES (:owner, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Irkutsk')::date, 1)
        ON CONFLICT (owner, day) DO UPDATE SET used=rag.quota.used+1
        WHERE rag.quota.used < 20 RETURNING used""",
        owner=owner,
    )
    return result.scalar_one_or_none() is not None
