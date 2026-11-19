"""Result deduplication must preserve provenance and source/code text exactly."""
import copy

from backend.public_knowledge import unique_ranked


def test_duplicates_refill_limit_and_preserve_first_ranked_citation():
    rows = [
        {"id": "rank1", "text": "Water fact", "version_id": "v1", "line_start": 44, "score": .5},
        {"id": "repeated-tab", "text": "Water fact", "version_id": "v1", "line_start": 229, "score": .4},
        {"id": "other-source", "text": "Water fact", "version_id": "v2", "line_start": 8, "score": .3},
        {"id": "distinct", "text": "Second fact", "version_id": "v3", "line_start": 9, "score": .2},
    ]
    before = copy.deepcopy(rows)
    result = unique_ranked(rows, 2)
    assert [row["id"] for row in result] == ["rank1", "distinct"]
    assert result[0] is rows[0] and result[0]["line_start"] == 44
    assert rows == before  # No mutation of stored text, score, or attribution.


def test_code_whitespace_and_unicode_differences_are_not_silently_normalized():
    texts = ["if ready:\n    run()", "if ready:\nrun()", "ماء", "مَاء", "same", "same\n"]
    rows = [{"id": str(i), "text": text} for i, text in enumerate(texts)]
    assert unique_ranked(rows, 10) == rows
    assert unique_ranked(rows, 0) == []
    assert unique_ranked([], 5) == []
