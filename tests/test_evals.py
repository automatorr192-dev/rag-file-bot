from evals.score import grounded


def test_grounded_rejects_added_return_conditions():
    case = {"sources": ["Правила возврата.txt"], "expected": ["14"],
            "forbidden": ["неразрушенной упаковки"]}
    sources = [{"name": "Правила возврата.txt"}]
    assert grounded(("Вернуть можно в течение 14 дней.", sources), case)
    assert not grounded(("14 дней при наличии неразрушенной упаковки.", sources), case)
    assert not grounded(("Вернуть можно в течение 14 дней.", []), case)
