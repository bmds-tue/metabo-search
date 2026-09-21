"""Vocab snapshots — pinned counts + mapping sanity.

The vendored lists freeze the workbench's controlled vocabularies
(disease 259, source 328, species 506).  Drift → these fail → re-capture
fixtures with the refresh note (docs/plan-metabolomics-workbench.md §10).
"""

from conftest import FIXTURES_DIR

from metabo_search.repositories.workbench.vocab import Vocab


def test_pinned_distinct_counts():
    import json
    v = Vocab.from_fixtures(FIXTURES_DIR)
    assert len(v.diseases) == 259          # disease map distinct values
    assert len(v.sources) == 328           # source map distinct values
    species_rows = json.loads((FIXTURES_DIR / "vocab_species.json")
                              .read_text())["values"]
    # the species endpoint returns (Latin, Common) rows; some Latin or Common
    # names repeat across rows, so the derived maps are smaller:
    assert len(species_rows) == 506        # captured rows
    assert len(v.latin_to_common) == 486   # unique Latin names
    assert len(v.species_common) == 450    # unique common names (= slot vocab)


def test_common_species_mapping_has_expected_pairs():
    v = Vocab.from_fixtures(FIXTURES_DIR)
    assert v.latin_to_common["Homo sapiens"] == "Human"
    assert v.latin_to_common["Mus musculus"] == "Mouse"
    assert v.latin_to_common["Rattus norvegicus"] == "Rat"


def test_all_aliases_resolve_onto_real_values():
    from metabo_search.repositories.workbench.matcher import ALIASES
    v = Vocab.from_fixtures(FIXTURES_DIR)
    all_values = v.diseases | v.sources
    for term, value in ALIASES.items():
        assert value in all_values, \
            f"alias {term!r} → {value!r} is not a canonical value"


def test_from_corpus_derivation_matches_fixture_shapes():
    from metabo_search.repositories.workbench.corpora import load_corpus_fixtures
    corpus = load_corpus_fixtures(FIXTURES_DIR)
    v = Vocab.from_corpus(corpus)
    # corpus subset fixtures contain these values at minimum
    assert "Diabetes" in v.diseases
    assert "Blood" in v.sources
    assert "Human" in v.species_common