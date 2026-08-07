"""Build structured comparison tables from scored candidates."""

from __future__ import annotations

from mtbls_agent.models import (
    ComparisonReport,
    RequirementProfile,
    ScoredCandidate,
)


def build_comparison_table(
    scored: list[ScoredCandidate],
    profile: RequirementProfile | None = None,
) -> ComparisonReport:
    """Build a structured comparison table from scored candidates.

    The table has one row per candidate and columns for key criteria
    (study ID, title, organism, technique, sample count, raw files,
    completeness, overall score, hard-passed status).
    """
    columns = [
        "study_id",
        "title",
        "organisms",
        "techniques",
        "samples",
        "raw_files",
        "completeness",
        "score",
        "hard_pass",
        "doi",
    ]

    rows = []
    for sc in scored:
        c = sc.candidate
        row = [
            c.study_id,
            c.title[:80],
            ", ".join(
                o.term if hasattr(o, "term") else str(o)
                for o in (c.organisms or [])
            )[:60],
            _get_technique_summary(c),
            str(c.sample_count or "?"),
            str(c.raw_file_count or "0"),
            f"{c.metadata_completeness:.0%}",
            f"{sc.score.overall:.2f}",
            "✓" if sc.score.hard_passed else "✗",
            (c.publications[0].doi if c.publications else ""),
        ]
        rows.append(row)

    return ComparisonReport(
        candidates=scored,
        table_columns=columns,
        table_rows=rows,
        query_profile=profile,
    )


def _get_technique_summary(candidate) -> str:
    """Get a short technique summary string."""
    techs = set()
    if candidate.assay_techniques:
        for at in candidate.assay_techniques:
            if isinstance(at, dict):
                name = at.get("name", "") or at.get("technique", "")
                if name:
                    techs.add(name)
    for a in candidate.assays:
        if a.technique_name:
            techs.add(a.technique_name)
    return ", ".join(sorted(techs))[:60] or "?"