"""Real-world sample<->file linking regression tests (offline, no network).

Fixtures are genuine filenames captured from MetaboLights studies:
MTBLS719 (dementia urine), MTBLS1375 (LipidCreator plasma),
MTBLS78 (lipid co-culture), MTBLS640 (mouse NMR), MTBLS1333 (eicosanoids).
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metabo_search.manifest import _sample_matches, SampleManifest
from metabo_search.downloader import _detect_ext, _infer_sample_name, _categorize
from metabo_search.sample_gen import (
    collect_sample_contexts,
    prepare_samples,
    parse_study_profile,
    apply_recipe,
)
from metabo_search.models import StudyCandidate, OntologyTerm


# ── Token fallback matcher (used when a sample is NOT in the assay map) ──


def test_mtbls1375_token_match():
    # Sample Name == file stem (flat FILES/)
    assert _sample_matches("1_LTR_1_A-1", "FILES/1_LTR_1_A-1.mzML") is True
    assert _sample_matches("1_LTR_1_A-1", "FILES/1_LTR_1_A-1.d.zip") is True
    # wrong sample number must NOT match
    assert _sample_matches("1_LTR_1_A-1", "FILES/1_LTR_1_A-2.mzML") is False
    assert _sample_matches("1_LTR_1_A-1", "FILES/2_LTR_1_A-1.mzML") is False


def test_mtbls719_no_false_positive_on_cryptic_names():
    # Sample DCR00004_U has NO token overlap with ALZ_RNEG_ToF03_U4W15.mzML.
    # The fallback must NOT falsely link them - that is why the assay map exists.
    assert _sample_matches(
        "DCR00004_U", "FILES/DERIVED_FILES/U_RNEG_mzML/ALZ_RNEG_ToF03_U4W15.mzML"
    ) is False


def test_mtbls78_nested_subdir_token_match():
    # Real Thermo .raw names under a co-culture subdirectory.
    assert _sample_matches(
        "Luca140818_lipid_Neg_co_culture_1",
        "FILES/LCMS_Co-culture/Luca140818_lipid_Neg_co_culture_1.raw",
    ) is True
    # Replicate boundary: _1 vs _2 must differ
    assert _sample_matches(
        "Luca140818_lipid_Neg_co_culture_1",
        "FILES/LCMS_Co-culture/Luca140818_lipid_Neg_co_culture_2.raw",
    ) is False
    # Blank pos/neg must not collide
    assert _sample_matches(
        "Blank_prep_no_IS_Neg_0uL", "FILES/LCMS_Co-culture/Blank_prep_no_IS_Neg_0uL.raw"
    ) is True
    assert _sample_matches(
        "Blank_prep_no_IS_Neg_0uL", "FILES/LCMS_Co-culture/Blank_prep_no_IS_Pos_0uL.raw"
    ) is False


def test_mtbls1333_punctuation_laden_names():
    # Tokens with parens/hyphens: "5(6)-EET-5(6)-EET-d11"
    assert _sample_matches(
        "5(6)-EET-5(6)-EET-d11",
        "FILES/5(6)-EET-5(6)-EET-d11-_M-H_1--0000161_fip.tsv",
    ) is True


# ── Assay-map linking (the primary, exact path) ──


def _mtbls719_shaped_candidate():
    c = StudyCandidate(study_id="MTBLS719")
    c.sample_file_parsed = True
    # Real sample rows: Source Name numeric-ish, Sample Name with _U
    c.sample_metadata = [
        {"Source Name": "DCR00004", "Sample Name": "DCR00004_U",
         "Characteristics[Organism]": "Homo sapiens",
         "Characteristics[Organism part]": "urine",
         "Factor Value[Gender]": "Male", "Factor Value[Age]": "79"},
        {"Source Name": "MTBLS_QC", "Sample Name": "MTBLS_QC",
         "Characteristics[Organism]": "Homo sapiens",
         "Characteristics[Organism part]": "urine",
         "Characteristics[Sample type]": "Quality Control"},
    ]
    c.sample_metadata_fields = [
        "Source Name", "Sample Name", "Characteristics[Organism]",
        "Characteristics[Organism part]", "Characteristics[Sample type]",
        "Factor Value[Gender]", "Factor Value[Age]",
    ]
    c.organisms = [OntologyTerm(term="Homo sapiens")]
    c.organism_parts = [OntologyTerm(term="urine")]
    c.assays = []
    # sample_file_map keys are the assay "Sample Name" (e.g. DCR00004_U)
    c.sample_file_map = {
        "DCR00004_U": {
            "raw": ["FILES/RAW_FILES/Alzheimer_Urine_RNEG.PRO/ALZ_RNEG_ToF03_U4W15.raw.zip",
                    "FILES/RAW_FILES/Alzheimer_Urine_HPOS.PRO/ALZ_HPOS_ToF07_U4W15.raw.zip"],
            "derived": ["FILES/DERIVED_FILES/U_RNEG_mzML/ALZ_RNEG_ToF03_U4W15.mzML"],
        },
        "MTBLS_QC": {"raw": ["FILES/RAW_FILES/QC.raw"], "derived": ["FILES/QC.mzML"]},
    }
    return c


def test_manifest_links_by_sample_name_map():
    """Primary path: assay `sample_file_map` keyed by Sample Name attaches
    the REAL ALZ_ file names, even though Source Name == DCR00004 differs."""
    c = _mtbls719_shaped_candidate()
    from metabo_search.sample_gen import SampleDescription
    descs = [SampleDescription("MTBLS719", "DCR00004_U", "urine, alzheimer.", ["qc"])]
    m = SampleManifest.build([c], sentences_map={"MTBLS719": descs})
    e = m.samples["MTBLS719"][0]
    assert e.sample_name == "DCR00004_U"
    assert any("ALZ_RNEG_ToF03_U4W15.mzML" in d for d in e.derived_data_files)
    assert any("ALZ_HPOS_ToF07_U4W15.raw.zip" in r for r in e.raw_data_files)
    assert e.sentence == "urine, alzheimer."


def test_collect_contexts_resolves_assay_map():
    c = _mtbls719_shaped_candidate()
    ctx = collect_sample_contexts(c)
    by_name = {x.sample_name: x for x in ctx}
    d = by_name["DCR00004_U"]
    assert any("ALZ_RNEG_ToF03_U4W15" in f for f in d.derived_data_files)
    # QC sample stays QC-flavoured even though it links to ALZ files
    qc = by_name["MTBLS_QC"]
    assert qc.sample_type == "Quality Control"


# ── Extension / category / stem parsing on real names ──


def test_detect_ext_real():
    assert _detect_ext("1_LTR_1_A-1.d.zip") == ".d.zip"    # compound
    assert _detect_ext("Luca140818_lipid_Neg_1.raw") == ".raw"
    assert _detect_ext("ALZ_RNEG_ToF03_U4W15.mzML") == ".mzml"
    assert _detect_ext("1.zip") == ".zip"                  # MTBLS640 bare


def test_infer_sample_name_real():
    assert _infer_sample_name("1_LTR_1_A-1.d.zip") == "1_LTR_1_A-1"
    assert _infer_sample_name("ALZ_RNEG_ToF03_U4W15.mzML") == "ALZ_RNEG_ToF03_U4W15"
    assert _infer_sample_name("Luca140818_lipid_Neg_co_culture_1.raw") == \
        "Luca140818_lipid_Neg_co_culture_1"


def test_categorize_real_dirs():
    # mzML under a RAW_ folder is raw, not derived
    assert _categorize(".mzml", "FILES/RAW_FILES/Alzheimer_Urine_RNEG.PRO/x.mzML") == "raw"
    # derived folder
    assert _categorize(".mzml", "FILES/DERIVED_FILES/U_RNEG_mzML/x.mzML") == "derived"
    # flat co-culture raw by extension
    assert _categorize(".raw", "FILES/LCMS_Co-culture/x.raw") == "raw"
    # MTBLS640 NMR just .zip under RAW_FILES
    assert _categorize(".zip", "FILES/RAW_FILES/1.zip") == "raw"
    # MTBLS1333 fip tables = other
    assert _categorize(".tsv", "FILES/5-HETE-5-HETE-d8-_M-H_1--0000145_fip.tsv") == "other"


# ── Sentence recipe decodes real MTBLS719 disease codes ──


def test_recipe_decodes_real_alz_codes():
    c = _mtbls719_shaped_candidate()
    prof = parse_study_profile(json.dumps({
        "codes": {"ALZ": "Alzheimer's disease", "CTL": "cognitively normal control"},
        "sentence_template": "Homo sapiens {tissue} from an {disease} patient.",
        "slot_sources": {"tissue": {"type": "tissue"},
                         "disease": {"type": "code", "field": "data_files"}},
        "qc_string": "quality control sample", "study_context": "."}))
    ctx = next(x for x in collect_sample_contexts(c) if x.sample_name == "DCR00004_U")
    d = apply_recipe(ctx, prof)
    assert "Alzheimer's disease" in d.sentence
