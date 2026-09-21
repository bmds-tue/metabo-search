"""Vocabulary matcher — real phrasing against the real (vendored) lists."""


from conftest import FIXTURES_DIR

from metabo_search.repositories.workbench.matcher import (
    SCORE_CUTOFF,
    map_latin_to_common,
    match,
)
from metabo_search.repositories.workbench.vocab import Vocab

VOCAB = Vocab.from_fixtures(FIXTURES_DIR)


# suffered a separate file... plural name kept for convention: these test
# the matcher, which is the same decision surface the pipeline uses.


def test_similarity_hits_with_clear_margins():
    cases = {
        "alzheimer": (VOCAB.diseases, "Alzheimers disease"),
        "alzheimers": (VOCAB.diseases, "Alzheimers disease"),
        "alzheimer disease": (VOCAB.diseases, "Alzheimers disease"),
        "diabetes": (VOCAB.diseases, "Diabetes"),
        "parkinsons": (VOCAB.diseases, "Parkinsons disease"),
        "depression": (VOCAB.diseases, "Depression"),
        "urine": (VOCAB.sources, "Urine"),
    }
    for term, (choices, expected) in cases.items():
        got = match(term, choices)
        assert got.value == expected, f"{term!r}: {got.value}"
        assert got.confident and got.score >= SCORE_CUTOFF


def test_tie_and_offtarget_abstain_without_slot():
    """Coin-flips and off-topic sentences never claim a canonical value."""
    # 'brain' is EXACTLY the canonical 'Brain' → the exact-value preference
    # resolves it (it never was a 50/50: picking the exact value is right);
    # a genuine coin-flip like Valley fever/Hay fever still abstains.
    assert match("brain", VOCAB.sources).value == "Brain"
    assert match("fever", VOCAB.diseases).value is None  # tie, no exact
    assert match("serum metabolomics", VOCAB.diseases).value is None
    assert match("gout", VOCAB.diseases).value is None   # 68 < cutoff


def test_alias_bridges_semantic_gaps():
    assert match("plasma", VOCAB.sources).value == "Blood"
    assert match("blood plasma", VOCAB.sources).value == "Blood"
    assert match("cell line", VOCAB.sources).value == "Cultured cells"
    assert match("cell lines", VOCAB.sources).value == "Cultured cells"
    assert match("type 2 diabetes", VOCAB.diseases).value == "Diabetes"


def test_garbage_and_empty_abstain():
    assert match("sdfjklwer", VOCAB.diseases).value is None
    assert match("", VOCAB.diseases).value is None
    assert match("zzz qqq", VOCAB.sources).value is None


def test_by_alias_flag_and_alternates():
    r = match("plasma", VOCAB.sources)
    assert r.by_alias is True
    assert r.value == "Blood"
    assert r.alternates          # original similarity candidates preserved


def test_study_counts_come_through():
    counts = {"Diabetes": 138, "Alzheimers disease": 27}
    r = match("diabetes", VOCAB.diseases, study_counts=counts)
    assert r.study_count == 138


def test_latin_to_common_mapping():
    l2c = VOCAB.latin_to_common
    assert map_latin_to_common("Rattus norvegicus", l2c, VOCAB.species_common) \
        == "Rat"
    assert map_latin_to_common("Homo sapiens", l2c, VOCAB.species_common) \
        == "Human"
    assert map_latin_to_common("Human", l2c, VOCAB.species_common) == "Human"
    assert map_latin_to_common("Felis catus", l2c, VOCAB.species_common) == "Cat"
    # genuinely absent species → None (caller leaves the slot empty)
    assert map_latin_to_common("Ornithorhynchus anatinus", l2c,
                               VOCAB.species_common) is None


def test_match_result_fmt_is_human_parseable():
    r = match("plasma", VOCAB.sources)
    assert "Blood" in r.fmt()