"""Keyword scoring and the hybrid blend."""

import pytest

from crewops.search.hybrid import blend, bm25, is_flat, normalize, tokens


def test_tokens_keep_ids_whole_and_in_parts():
    assert tokens("What does RULE-FDP-01 say about DX402?") == [
        "rule-fdp-01",
        "rule",
        "fdp",
        "01",
        "say",
        "about",
        "dx402",
    ]


def test_fdp_matches_the_rule_that_names_it():
    docs = ["RULE-FDP-01: Max flight duty period 13h", "RULE-REST-04: Min 12h rest"]
    scores = bm25("FDP limit", docs)
    assert scores[0] > scores[1] == 0.0


def test_bm25_ranks_the_document_sharing_rare_terms():
    docs = [
        "Max flight duty period 13h, reduced 0.5h per sector",
        "Min 12h rest between release and next report",
        "Crew must hold a valid rating for the aircraft type",
    ]
    scores = bm25("minimum rest before the next report", docs)
    assert scores.index(max(scores)) == 1
    assert scores[0] == 0.0


def test_bm25_edge_cases():
    assert bm25("anything", []) == []
    assert bm25("the of and", ["some text"]) == [0.0]


def test_normalize_and_flat():
    assert normalize([2.0, 4.0, 3.0]) == [0.0, 1.0, 0.5]
    assert normalize([5.0, 5.0]) == [0.0, 0.0]
    assert is_flat([1.0, 1.0]) and not is_flat([1.0, 1.1])


def test_blend_weights_both_sides():
    assert blend([0.0, 10.0], [0.2, 0.8], alpha=0.5) == [0.0, 1.0]
    mixed = blend([0.0, 10.0, 5.0], [0.9, 0.1, 0.5], alpha=0.5)
    assert mixed == pytest.approx([0.5, 0.5, 0.5])


def test_flat_side_is_dropped_not_blended_in():
    # A paraphrase sharing no words: keywords all zero, so semantics alone decide,
    # and the best match can still reach 1.0 instead of being capped at 0.5.
    assert blend([0.0, 0.0, 0.0], [0.3, 0.9, 0.6]) == pytest.approx([0.0, 1.0, 0.5])
    assert blend([1.0, 3.0], [0.5, 0.5]) == [0.0, 1.0]
    assert blend([1.0, 1.0], [0.5, 0.5]) == [0.0, 0.0]


def test_blend_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        blend([1.0], [0.1, 0.2])
    assert blend([], []) == []
