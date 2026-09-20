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
