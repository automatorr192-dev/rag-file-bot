def recall(results, expected):
    return len({row["name"] for row in results} & set(expected)) / len(expected)


def grounded(response, case):
    answer, sources = response
    names = {source["name"] for source in sources}
    return names == set(case["sources"]) and all(
        token.casefold() in answer.casefold() for token in case["expected"]
    ) and not any(token.casefold() in answer.casefold() for token in case.get("forbidden", []))
