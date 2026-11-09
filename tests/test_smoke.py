import json

import pytest

from rag import NO_ANSWER, FileRejected, extract, validate_response


def test_extract_txt_keeps_location():
    assert extract("notes.txt", "Привет".encode()) == [("фрагмент 1", "Привет")]


@pytest.mark.parametrize("content", [b"", b"\xff"])
def test_reject_empty_or_invalid_txt(content):
    with pytest.raises(FileRejected):
        extract("notes.txt", content)


@pytest.mark.parametrize("citations", [[2], [True], [], [1, 2]])
def test_invalid_citations_reject_entire_answer(citations):
    raw = json.dumps({"answer": "Ответ", "citations": citations})
    assert validate_response(raw, [{"id": 1}]) == (NO_ANSWER, [])


def test_valid_citations_preserve_source():
    hit = {"id": 1, "body": "Текст"}
    assert validate_response('{"answer":"Ответ","citations":[1,1]}', [hit]) == ("Ответ", [hit])
