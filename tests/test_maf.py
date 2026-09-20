"""Real-world MAF (metabolite assignment file) parsing + filtering.

MAF files are the ``m_*.tsv`` ISA-Tab files: one row per identified
metabolite (database_identifier, name, formula, m/z, retention_time,
per-sample abundance columns).  The inspector parses them into
``metabolite_count`` + ``maf_files_parsed`` on the StudyCandidate.

Filenames below are the ACTUAL names seen on MetaboLights studies
(MTBLS1375, MTBLS78, MTBLS719) — some are long, hyphenated, versioned.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtbls_agent.inspector import (  # noqa: E402
    RE_MAF,
    _parse_maf_files,
    load_study_from_isa,
)
from mtbls_agent.models import StudyCandidate  # noqa: E402
from mtbls_agent.scorer import _score_one, filter_by_maf  # noqa: E402

# ── Real-world MAF filenames (observed on MetaboLights) ─────────────

REAL_MAF_NAMES = [
    # MTBLS1375 — long, hyphenated, _v2 version marker + _maf suffix
    "m_MTBLS1375_LC-MS_positive_reverse-phase_metabolite_profiling_v2_maf.tsv",
    # Generic/subdirectory-style variants
    "m_MTBLS78_LCMS_Co-culture_maf.tsv",
    "m_MTBLS719_urine_dementia_maf.tsv",
]

REAL_NON_MAF = [
    # Must NOT match: assay / sample / investigation files
    "a_MTBLS1375_LC-MS_positive_reverse-phase_metabolite_profiling.txt",
    "s_MTBLS1375.txt",
    "i_Investigation.txt",
    # Wrong extension or wrong prefix
    "m_MTBLS1375_maf.txt",       # .txt, not .tsv
    "mm_MTBLS1375_maf.tsv",      # mm_ prefix
    "maf_MTBLS1375.tsv",         # not m_-prefixed at start
]

# Minimal but realistic MAF content (header + a few rows, tab-separated).
# Real MAF files (e.g. MTBLS1375) leave `database_identifier` empty and put
# the metabolite name in `metabolite_identification`.
MAF_TSV = (
    "database_identifier\tchemical_formula\tmetabolite_identification\t"
    "mass_to_charge\tretention_time\tSpecies_S1\tSpecies_S2\n"
    "\tC55H100O6\tCE 16:1\t640.6027\t11.44\t110460\t103851\n"
    "\tC55H98O6\tCE 16:2\t638.5871\t11.25\t3543\t3685\n"
    "\tC41H56O10\tCer d18:1/16:0\t520.5088\t8.05\t46459\t40357\n"
    # trailing blank line (common in real files)
    "\n"
)


def _write_isa_dir(tmp_path: Path, maf_name: str = REAL_MAF_NAMES[0]) -> Path:
    """Write an ISA directory with a single real-world-named MAF file."""
    d = tmp_path / "isa"
    d.mkdir()
    (d / maf_name).write_text(MAF_TSV)
    return d


# ── Filename matching ──────────────────────────────────────────────


def test_maf_regex_matches_real_world_names():
    for name in REAL_MAF_NAMES:
        assert RE_MAF.match(name), f"should match real MAF name: {name}"


def test_maf_regex_rejects_non_maf_isa_files():
    for name in REAL_NON_MAF:
        assert not RE_MAF.match(name), f"should NOT match: {name}"


def test_maf_regex_extracts_study_slug():
    m = RE_MAF.match(REAL_MAF_NAMES[0])
    assert m is not None
    # the captured group is the whole study/assay slug
    assert "MTBLS1375" in m.group(1)


# ── Parsing (offline, via load_study_from_isa / _parse_maf_files) ──


def test_parse_maf_counts_only_nonempty_first_column(tmp_path):
    d = _write_isa_dir(tmp_path)
    enrichment = {"metabolite_count": None, "maf_files_parsed": False}
    _parse_maf_files(enrichment, "MTBLS1375", d)
    assert enrichment["maf_files_parsed"] is True
    # 3 populated metabolite_identification rows; the empty database_identifier
    # column must NOT zero the count (real MAF files look like this).
    assert enrichment["metabolite_count"] == 3


def test_load_study_from_isa_populates_maf(tmp_path):
    d = _write_isa_dir(tmp_path)
    cand = load_study_from_isa("MTBLS1375", d)
    assert cand.maf_files_parsed is True
    assert cand.metabolite_count == 3


def test_parse_real_mtbls1375_maf_counts_286(tmp_path):
    """The ACTUAL MTBLS1375 MAF from the mirror: 286 data rows, first column
    empty, names in metabolite_identification.  Regression guard for the
    empty-database_identifier bug."""
    real = Path("/tmp/maf_example.tsv")
    if not real.exists():
        import pytest
        pytest.skip("real MAF fixture not present (downloaded earlier)")
    d = tmp_path / "isa"
    d.mkdir()
    (d / REAL_MAF_NAMES[0]).write_text(real.read_text())
    cand = load_study_from_isa("MTBLS1375", d)
    assert cand.maf_files_parsed is True
    assert cand.metabolite_count == 286


def test_load_study_without_maf_leaves_flags_false(tmp_path):
    d = tmp_path / "isa"
    d.mkdir()
    (d / "s_MTBLS1375.txt").write_text("Source Name\tSample Name\nS1\tA-1\n")
    cand = load_study_from_isa("MTBLS1375", d)
    assert cand.maf_files_parsed is False
    assert cand.metabolite_count is None


# ── Filtering (post-inspection) ────────────────────────────────────


def test_filter_by_maf_keeps_only_maf_studies():
    with_maf = StudyCandidate(study_id="MTBLS1375")
    with_maf.maf_files_parsed = True
    with_maf.metabolite_count = 512
    without = StudyCandidate(study_id="MTBLS78")
    # deep-inspected but no MAF present
    without.maf_files_parsed = False

    kept = filter_by_maf([without, with_maf])
    assert [c.study_id for c in kept] == ["MTBLS1375"]

    kept = filter_by_maf([without, with_maf], require_maf=False)
    assert [c.study_id for c in kept] == ["MTBLS78"]


def test_filter_by_maf_min_metabolites():
    a = StudyCandidate(study_id="A"); a.maf_files_parsed = True; a.metabolite_count = 10
    b = StudyCandidate(study_id="B"); b.maf_files_parsed = True; b.metabolite_count = 500
    kept = filter_by_maf([a, b], min_metabolites=100)
    assert [c.study_id for c in kept] == ["B"]


def test_filter_by_maf_preserves_input_order():
    a = StudyCandidate(study_id="A"); a.maf_files_parsed = True; a.metabolite_count = 1
    b = StudyCandidate(study_id="B"); b.maf_files_parsed = False
    c = StudyCandidate(study_id="C"); c.maf_files_parsed = True; c.metabolite_count = 2
    kept = filter_by_maf([c, a, b])
    assert [x.study_id for x in kept] == ["C", "A"]


# ── Scoring integration (RequirementProfile hard/nice) ─────────────


def test_score_hard_maf_requirement():
    from mtbls_agent.models import RequirementProfile, StudyRequirements

    with_maf = StudyCandidate(study_id="MTBLS1375")
    with_maf.maf_files_parsed = True
    with_maf.metabolite_count = 512
    no_maf = StudyCandidate(study_id="MTBLS78")

    prof = RequirementProfile(
        hard=StudyRequirements(has_maf=True, min_metabolites=100)
    )
    s_ok = _score_one(with_maf, prof)
    assert s_ok.hard_passed is True
    assert s_ok.per_criterion["maf (hard)"] == 1.0
    assert s_ok.per_criterion["min_metabolites (hard)"] == 1.0

    s_fail = _score_one(no_maf, prof)
    assert s_fail.hard_passed is False
    assert "MAF" in s_fail.hard_fail_reasons[0]


def test_score_hard_no_maf_requirement():
    from mtbls_agent.models import RequirementProfile, StudyRequirements

    with_maf = StudyCandidate(study_id="MTBLS1375")
    with_maf.maf_files_parsed = True
    with_maf.metabolite_count = 512
    no_maf = StudyCandidate(study_id="MTBLS78")

    prof = RequirementProfile(hard=StudyRequirements(has_maf=False))
    assert _score_one(with_maf, prof).hard_passed is False
    assert _score_one(no_maf, prof).hard_passed is True


def test_score_nice_maf_rewards_metabolite_rich_studies():
    from mtbls_agent.models import RequirementProfile, StudyRequirements

    rich = StudyCandidate(study_id="R")
    rich.maf_files_parsed = True
    rich.metabolite_count = 1000
    poor = StudyCandidate(study_id="P")
    poor.maf_files_parsed = True
    poor.metabolite_count = 5

    prof = RequirementProfile(
        nice_to_have=StudyRequirements(min_metabolites=200)
    )
    s_rich = _score_one(rich, prof)
    s_poor = _score_one(poor, prof)
    assert s_rich.overall > s_poor.overall
    assert s_rich.per_criterion["min_metabolites (nice)"] > 0


# ── Analysis (analyze_maf_files) ───────────────────────────────────


# A realistic NAMED MAF: metadata columns mostly empty, name in
# metabolite_identification, sample columns like MTBLS1375.
NAMED_MAF_TSV = (
    "database_identifier\tmetabolite_identification\tmass_to_charge\t"
    "retention_time\t5_NIST_A-1\t11_PLASMA_H002_A-1\n"
    "\tCE 16:1\t640.6027\t11.44\t110460\t99603\n"
    "\tCE 16:2\t638.5871\t11.25\t3543\t4057\n"
    "\tCer d18:1/16:0\t520.5088\t8.05\t46459\t39581\n"
)

# A m/z-ONLY MAF: no names, no identifiers — just scores plus samples.
MZONLY_MAF_TSV = (
    "mass_to_charge\tretention_time\tS1\tS2\tS3\n"
    "640.6027\t11.44\t110460\t99603\t8000\n"
    "638.5871\t11.25\t3543\t4057\t10000\n"
)

# An IDENTIFIER-ONLY MAF: uri/database ids populated, name column empty.
IDONLY_MAF_TSV = (
    "database_identifier\tmetabolite_identification\tmass_to_charge\tS1\n"
    "SLM:000500345\t\t640.6027\t110460\n"
    "SLM:000500344\t\t638.5871\t3543\n"
)


from mtbls_agent.maf import (  # noqa: E402
    analyze_maf_files,
    render_maf_summary,
)


def _write(tmp_path, name, content) -> Path:
    d = tmp_path / "isa"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(content)
    return d


def test_analyze_named_maf_counts_and_detects_names(tmp_path):
    d = _write(tmp_path, REAL_MAF_NAMES[0], NAMED_MAF_TSV)
    res = analyze_maf_files("MTBLS1375", isa_dir=d)
    assert len(res) == 1
    a = res[0]
    assert a.metabolite_count == 3
    assert a.sample_count == 2
    assert a.sample_columns == ["5_NIST_A-1", "11_PLASMA_H002_A-1"]
    assert a.has_names is True
    assert a.named_count == 3
    assert a.annotation_level == "named"
    assert a.mz_only is False
    assert a.examples[:3] == ["CE 16:1", "CE 16:2", "Cer d18:1/16:0"]
    assert "286" not in a.summary  # no stale counts


def test_analyze_mz_only_maf(tmp_path):
    d = _write(tmp_path, "m_MTBLS78_LC-MS_maf.tsv", MZONLY_MAF_TSV)
    res = analyze_maf_files("MTBLS78", isa_dir=d)
    assert len(res) == 1
    a = res[0]
    assert a.metabolite_count == 2
    assert a.sample_count == 3
    assert a.has_names is False
    assert a.has_identifiers is False
    assert a.annotation_level == "mz_only"
    assert a.mz_only is True
    assert a.mz_count == 2
    assert "m/z only" in a.summary


def test_analyze_identifier_only_maf(tmp_path):
    d = _write(tmp_path, "m_MTBLS719_maf.tsv", IDONLY_MAF_TSV)
    res = analyze_maf_files("MTBLS719", isa_dir=d)
    a = res[0]
    assert a.metabolite_count == 2
    assert a.sample_count == 1
    assert a.has_names is False
    assert a.has_identifiers is True
    assert a.identified_count == 2
    assert a.annotation_level == "identified"
    assert a.examples == ["SLM:000500345", "SLM:000500344"]


def test_analyze_isfa_dir_subdirectory_layout(tmp_path):
    """analyze_maf_files(id, root) finds files under root/<study_id>/, which
    is exactly the download_maf_files output layout."""
    sub = tmp_path / "maf_dl" / "MTBLS1375"
    sub.mkdir(parents=True)
    (sub / REAL_MAF_NAMES[0]).write_text(NAMED_MAF_TSV)
    res = analyze_maf_files("MTBLS1375", isa_dir=tmp_path / "maf_dl")
    assert len(res) == 1
    assert res[0].file_name == REAL_MAF_NAMES[0]


def test_analyze_explicit_paths_and_sorted_params(tmp_path):
    d = _write(tmp_path, "m_MTBLS1375_maf.tsv", NAMED_MAF_TSV)
    p = d / "m_MTBLS1375_maf.tsv"
    res = analyze_maf_files("MTBLS1375", maf_paths=[str(p)], max_examples=2)
    assert len(res) == 1
    assert len(res[0].examples) == 2


def test_analyze_no_maf_returns_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert analyze_maf_files("MTBLS1", isa_dir=d) == []


def test_render_maf_summary_multiline(tmp_path):
    d = _write(tmp_path, "m_MTBLS1375_maf.tsv", NAMED_MAF_TSV)
    res = analyze_maf_files("MTBLS1375", isa_dir=d)
    s = render_maf_summary(res)
    assert "MTBLS1375" in s and "3 metabolites" in s and "2 samples" in s
