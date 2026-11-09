import json
import os

import pytest

import db
import rag
from rag import ingest


@pytest.mark.asyncio
async def test_ingest_isolation_history_and_delete(monkeypatch):
    url = os.getenv("RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires migrated disposable PostgreSQL with pgvector")
    monkeypatch.setenv("DATABASE_URL", url)
    engine = db.make_engine()
    vector = [1.0] + [0.0] * 383

    async def embeddings(inputs):
        return [vector for _ in inputs]

    async def fail(inputs):
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(rag, "embed", embeddings)
    owner = 987654321
    try:
        async with engine.begin() as conn:
            await db.query(conn, "DELETE FROM rag.documents WHERE owner=:owner", owner=owner)
            await db.clear(conn, owner, "files")
        assert await ingest(engine, owner, "guide.txt", b"product LX-410")
        assert not await ingest(engine, owner, "duplicate.txt", b"product LX-410")
        monkeypatch.setattr(rag, "embed", fail)
        with pytest.raises(RuntimeError):
            await ingest(engine, owner, "failed.txt", b"never indexed")
        async with engine.begin() as conn:
            hits = await db.search(conn, owner, "LX-410", vector)
            assert [hit["name"] for hit in hits] == ["guide.txt"]
            assert not await db.search(conn, owner + 1, "LX-410", vector)
            rows = await db.query(
                conn, "SELECT id FROM rag.documents WHERE owner=:owner", owner=owner
            )
            document_id = rows.scalar_one()
            await db.query(
                conn,
                """INSERT INTO rag.turns(owner,mode,question,answer,sources)
                VALUES (:owner,'files','question','answer',
                CAST(CAST(:sources AS text) AS jsonb))""",
                owner=owner,
                sources=json.dumps(hits),
            )
            assert not await db.delete_document(conn, owner + 1, document_id)
        async with engine.begin() as conn:
            assert await db.history(conn, owner, "files")
            assert not await db.history(conn, owner, "demo")
            assert await db.delete_document(conn, owner, document_id)
            assert not await db.history(conn, owner, "files")
            assert not await db.search(conn, owner, "LX-410", vector)
    finally:
        async with engine.begin() as conn:
            await db.query(conn, "DELETE FROM rag.documents WHERE owner=:owner", owner=owner)
            await db.clear(conn, owner, "files")
        await engine.dispose()
