import db
from models import embed
from prompts import ANSWER, REWRITE
from rag import complete, validate_response


async def run_case(engine, client, case):
    question = case["question"]
    if case.get("history"):
        question = (
            await complete(client, REWRITE, {"history": case["history"], "question": question})
        ).strip() or question
    (vector,) = await embed([question])
    async with engine.connect() as conn:
        results = {
            mode: await db.search(conn, 0, question, vector, mode)
            for mode in ("keyword", "semantic", "hybrid")
        }
    hits = results["hybrid"]
    raw = await complete(client, ANSWER, {"question": question, "sources": hits}, json_mode=True)
    return results, validate_response(raw, hits)
