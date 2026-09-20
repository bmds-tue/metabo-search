"""Regression tests for the sample-linking + sentence-generation fixes.

Run:  .venv-local/bin/python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metabo_search.manifest import SampleManifest
from metabo_search.sample_gen import (
    apply_recipe,
    parse_study_profile,
    prepare_samples,
    submit_samples,
    SampleDescription,
    SampleContext,
    SampleSentencesStore,
)
from metabo_search.downloader import _categorize
from metabo_search.models import StudyCandidate


def _hymet_study():
    """A StudyCandidate where Source Name (numeric) != Sample Name (WCQA-*)."""
    c = StudyCandidate(study_id="MTBLS_H")
    c.sample_file_parsed = True
    c.sample_metadata = [
        {"Source Name": "2533491", "Sample Name": "WCQA-073",
         "Characteristics[Organism]": "Homo sapiens",
         "Characteristics[Organism part]": "urine",
         "Factor Value[Intervention]": "OGTT"},
        {"Source Name": "2533492", "Sample Name": "WCQA-074",
         "Characteristics[Organism]": "Homo sapiens",
         "Characteristics[Organism part]": "urine",
         "Factor Value[Intervention]": "Fasting"},
    ]
    c.sample_metadata_fields = [
        "Source Name", "Sample Name", "Characteristics[Organism]",
        "Characteristics[Organism part]", "Factor Value[Intervention]",
    ]
    c.organisms = []
    c.organism_parts = []
    c.assays = []
    c.sample_file_map = {
        "WCQA-073": {"raw": ["FILES/WCQA-073.raw"], "derived": ["FILES/WCQA-073.mzML"]},
        "WCQA-074": {"raw": ["FILES/WCQA-074.raw"], "derived": ["FILES/WCQA-074.mzML"]},
    }
    return c


def test_manifest_links_by_sample_name_not_source_name():
    """Bug #1: manifest must link sentences+files by Sample Name (WCQA-*)."""
    c = _hymet_study()
    descs = [
        SampleDescription("MTBLS_H", "WCQA-073", "Homo sapiens urine, OGTT."),
        SampleDescription("MTBLS_H", "WCQA-074", "Homo sapiens urine, fasted."),
    ]
    m = SampleManifest.build([c], sentences_map={"MTBLS_H": descs})
    entries = m.samples["MTBLS_H"]
    assert len(entries) == 2
    assert entries[0].sentence == "Homo sapiens urine, OGTT.", "sentence not linked"
    assert entries[0].raw_data_files == ["FILES/WCQA-073.raw"], "files not linked"
    assert entries[1].raw_data_files == ["FILES/WCQA-074.raw"]


def test_factor_value_code_decoding():
    """Bug #2: a code slot pointing at a factor decodes OGTT -> meaning."""
    c = _hymet_study()
    prof = parse_study_profile(json.dumps({
        "codes": {"OGTT": "oral glucose tolerance test", "PAT": "patient"},
        "sentence_template": "Homo sapiens {tissue}, {intervention}.",
        "slot_sources": {
            "tissue": {"type": "tissue"},
            "intervention": {"type": "code", "field": "Intervention"},
        },
        "qc_string": "qc", "study_context": "."}))
    ctx = _context_for(c, 0)
    from metabo_search.sample_gen import apply_recipe
    d = apply_recipe(ctx, prof)
    assert "oral glucose tolerance test" in d.sentence


def _context_for(c, idx):
    from metabo_search.sample_gen import collect_sample_contexts
    return collect_sample_contexts(c)[idx]


def test_category_by_directory():
    """Bug #6: mzML under RAW_FILES classify as raw, not derived."""
    assert _categorize(".mzml", "FILES/RAW_FILES/x.mzML") == "raw"
    assert _categorize(".mzml", "FILES/DERIVED_FILES/x.mzML") == "derived"
    assert _categorize(".mzml") == "derived"
    assert _categorize(".raw.zip", "FILES/RAW_FILES/x.raw.zip") == "raw"


def test_revision_cache_does_not_clobber(tmp_path):
    """Bug #7: revising wording keeps the old revision available."""
    c = _hymet_study()
    store = SampleSentencesStore(tmp_path / "cache.json")
    t0 = prepare_samples(c, store, revision=0)
    # there is no LLM here; use a trivial profile for both revisions
    base = '{"codes":{},"sentence_template":"Homo sapiens {tissue}, {sample_type}.","slot_sources":{"tissue":{"type":"tissue"},"sample_type":{"type":"sample_type"}},"qc_string":"qc","study_context":"."}'
    submit_samples(t0, base)
    assert t0.cache_key in store

    from metabo_search.sample_gen import revise_samples, load_samples
    t1 = revise_samples(t0, base)
    assert t0.cache_key != t1.cache_key, "revision must change the key"
    assert t1.cache_key in store
    assert load_samples(t0) is not None, "old revision still present"