import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import db
import rag
from prompts import ANSWER, REWRITE
from rag import NO_ANSWER, answer_question


@asynccontextmanager
async def connection():
    yield object()


@pytest.mark.parametrize(("mode", "scope"), [("demo", 0), ("files", 42)])
async def test_followup_search_and_multiple_citations(monkeypatch, mode, scope):
    history = [{"question": "Как вернуть заказ?", "answer": "Срок возврата — 14 дней."}]
    hits = [{"id": 11, "body": "14 дней"}, {"id": 12, "body": "Курьер забирает возврат"}]

    async def read_history(conn, owner, current_mode):
        assert (owner, current_mode) == (42, mode)
        return history

    async def search(conn, owner, question, vector):
        assert owner == scope
        assert question == "Как вернуть заказ через курьера?"
        assert len(vector) == 384
        return hits

    async def embeddings(inputs):
        assert inputs == ["Как вернуть заказ через курьера?"]
        return [[1.0] * 384]

    async def complete(**kwargs):
        system, user = kwargs["messages"]
        payload = json.loads(user["content"])
        if system["content"] == REWRITE:
            assert payload["history"] == history
            answer = "Как вернуть заказ через курьера?"
        else:
            assert system["content"] == ANSWER
            assert payload["sources"] == hits
            answer = '{"answer":"Курьер забирает возврат в пределах 14 дней.","citations":[11,12]}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

    monkeypatch.setattr(db, "history", read_history)
    monkeypatch.setattr(db, "search", search)
    monkeypatch.setattr(rag, "embed", embeddings)
    engine = SimpleNamespace(connect=connection)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete)),
    )

    answer, citations = await answer_question(engine, client, 42, mode, "А через курьера?")
    assert "14 дней" in answer
    assert citations == hits


async def test_no_hits_skip_generation(monkeypatch):
    async def empty(*args):
        return []

    async def embeddings(inputs):
        return [[1.0] * 384]

    monkeypatch.setattr(db, "history", empty)
    monkeypatch.setattr(db, "search", empty)
    monkeypatch.setattr(rag, "embed", embeddings)
    engine = SimpleNamespace(connect=connection)
    client = SimpleNamespace()

    assert await answer_question(engine, client, 42, "files", "Вопрос") == (NO_ANSWER, [])


async def test_model_error_returns_retrieved_sources(monkeypatch):
    from openai import APIError

    hits = [{"id": 11, "body": "14 дней"}]

    async def no_history(*args):
        return []

    async def embeddings(inputs):
        return [[1.0] * 384]

    async def search(*args):
        return hits

    async def fail(*args, **kwargs):
        raise APIError("test outage", request=None, body=None)

    monkeypatch.setattr(db, "history", no_history)
    monkeypatch.setattr(db, "search", search)
    monkeypatch.setattr(rag, "embed", embeddings)
    monkeypatch.setattr(rag, "complete", fail)
    engine = SimpleNamespace(connect=connection)

    answer, sources = await answer_question(engine, None, 42, "demo", "Срок возврата?")
    assert answer.startswith("Модель временно недоступна.")
    assert sources == hits
