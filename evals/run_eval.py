import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

import db
from demo import DEMO
from evals.score import grounded, recall
from evals.target import run_case
from models import chat_client


async def main():
    cases = [
        json.loads(line)
        for line in Path(__file__).with_name("cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    engine = db.make_engine()
    scores = {mode: [] for mode in ("keyword", "semantic", "hybrid")}
    correct = []
    details = []
    try:
        async with engine.connect() as conn:
            rows = await db.query(conn, "SELECT name FROM rag.documents WHERE owner=0")
            if sorted(rows.scalars()) != sorted(DEMO):
                raise SystemExit("Run python demo.py on a clean demo corpus first.")
        async with chat_client(timeout=20) as client:
            for case in cases:
                results, response = await run_case(engine, client, case)
                if case["sources"]:
                    for mode, rows in results.items():
                        scores[mode].append(recall(rows, case["sources"]))
                passed = grounded(response, case)
                correct.append(passed)
                details.append({"question": case["question"], "passed": passed,
                                "answer": response[0],
                                "sources": [row["name"] for row in response[1]]})
                if not passed:
                    print(f"FAIL: {case['question']} -> {response[0]}")
    finally:
        await engine.dispose()
    output = Path(__file__).with_name("runs")
    output.mkdir(exist_ok=True)
    report = {"created_at": datetime.now(UTC).isoformat(), "cases": details,
              "retrieval": {mode: mean(values) for mode, values in scores.items()},
              "answer_accuracy": mean(correct)}
    (output / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    for mode, values in scores.items():
        print(f"{mode} Recall@5: {mean(values):.0%}")
    print(f"Answer and citations: {mean(correct):.0%} ({sum(correct)}/{len(correct)})")


if __name__ == "__main__":
    asyncio.run(main())
