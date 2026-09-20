"""MAF (metabolite assignment file) analysis — deterministic, LLM-light.

Answers the three questions a researcher asks first about a study's MAF
(``m_*.tsv``) files, without any LLM round-trip:

  * how many metabolites (data rows)?
  * how many samples (per-sample abundance columns)?
  * does it carry real metabolite names / identifiers, or only m/z scores?

The library does the parsing + counting; the agent reads the returned
``MafAnalysis`` objects (or the ready-made ``summary`` string) and decides.

Zero LLM calls.  Deterministic.  Input is either downloaded ``m_*.tsv`` paths
(e.g. from :func:`download_maf_files`) or an ISA directory containing them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from metabolights_utils.isatab.default.parser.isa_table_parser import (
    parse_isa_table_sheet_from_fs,
)

from mtbls_agent.inspector import RE_MAF

logger = logging.getLogger(__name__)

# ── Column classification ──────────────────────────────────────────

# Exact metadata column names in a MetaboLights MAF (lower-cased).
_METADATA_EXACT = frozenset({
    "database_identifier", "chemical_formula", "smiles", "inchi", "inchi_key",
    "name", "metabolite_identification", "metabolite_name", "mass_to_charge",
    "fragmentation", "modifications", "charge", "retention_time",
    "retention_index", "taxid", "species", "database", "database_version",
    "reliability", "uri", "search_engine", "search_engine_score",
    "annotation_confidence", "qc", "comment", "notes",
})

# Column-name hints for the metabolite *name* (preferred first).
_NAME_COL_HINTS = ("metabolite_identification", "metabolite_name",
                   "compound_name", "identification")

# Identifier-bearing columns (real molecule identifiers/structural info).
_IDENTIFIER_EXACT = frozenset({
    "database_identifier", "uri", "inchi", "inchi_key", "smiles",
    "chemical_formula", "pubchem", "chebi", "hmdb", "metlin", "splash",
    "swisslipids", "kegg",
})

# suffixes that mark a summary / identifier column even when the core name
# isn't in the exact set (e.g. SwissLipid_identifier, abundance_stdev_sub).
_METADATA_SUFFIXES = ("_identifier", "_sub", "_stdev", "_std_error",
                      "_mean", "_median", "_max", "_min")


def _is_metadata_col(col: str) -> bool:
    """True if a MAF column is metadata/summary, not a per-sample abundance."""
    c = col.strip().lower()
    if c in _METADATA_EXACT:
        return True
    if "abundance" in c:
        return True
    if any(c.endswith(s) for s in _METADATA_SUFFIXES):
        return True
    return False


def _is_identifier_col(col: str) -> bool:
    """True if a column carries a real metabolite identifier/structural info."""
    c = col.strip().lower()
    if c in _IDENTIFIER_EXACT:
        return True
    if c.endswith("_identifier"):
        return True
    return False


# ── Result model ────────────────────────────────────────────────────


@dataclass
class MafAnalysis:
    """What one MAF file contains, in answer-ready numbers.

    The :attr:`summary` property renders everything into one line the agent
    can paste straight into its answer / reasoning.
    """

    study_id: str = ""
    file_name: str = ""
    file_path: str = ""

    # ── counts ──
    metabolite_count: int = 0
    """Data rows = identified metabolite features (fullest populated column)."""
    sample_count: int = 0
    """Per-sample abundance columns in the MAF (= samples quantified)."""
    sample_columns: list[str] = field(default_factory=list)

    # ── annotation depth ──
    named_count: int = 0
    """Rows with a real metabolite name (metabolite_identification)."""
    identified_count: int = 0
    """Rows with a database identifier / structural info (uri, inchi, *_identifier…)."""
    mz_count: int = 0
    """Rows with a mass-to-charge value."""

    annotation_level: str = "empty"
    """'named' | 'identified' | 'mz_only' | 'empty' (precedence in that order)."""
    examples: list[str] = field(default_factory=list)
    """Up to N sample values from the name/identifier/mz column (first rows)."""

    parse_error: str = ""
    """Empty unless the file could not be parsed."""

    # ── boolean sugar (what the researcher actually asks) ──
    @property
    def has_names(self) -> bool:
        return self.named_count > 0

    @property
    def has_identifiers(self) -> bool:
        return self.identified_count > 0

    @property
    def mz_only(self) -> bool:
        return self.annotation_level == "mz_only"

    @property
    def summary(self) -> str:
        """One LLM-ready line, e.g.:

        ``MTBLS1375 m_..._v2_maf.tsv: 286 metabolites, 88 samples, named
        (286/286) — e.g. CE 16:1, CE 16:2…``
        """
        level = {
            "named": f"named ({self.named_count}/{self.metabolite_count})",
            "identified": f"identifiers only ({self.identified_count})",
            "mz_only": "m/z only — no names/identifiers",
            "empty": "empty / un-annotated",
        }[self.annotation_level]
        ex = ""
        if self.examples:
            shown = ", ".join(self.examples[:3])
            ex = f" — e.g. {shown}" + ("…" if len(self.examples) > 3 else "")
        line = (f"{self.study_id} {self.file_name}: {self.metabolite_count} "
                f"metabolites, {self.sample_count} samples, {level}{ex}")
        if self.parse_error:
            line += f" [parse error: {self.parse_error}]"
        return line


# ── Analysis ────────────────────────────────────────────────────────


def analyze_maf_files(
    study_id: str,
    isa_dir: str | Path | None = None,
    maf_paths: list[str | Path] | None = None,
    max_examples: int = 5,
) -> list[MafAnalysis]:
    """Analyze the MAF (``m_*.tsv``) files of a study — locally, no LLM.

    Provide either downloaded MAF paths (``maf_paths``, e.g. from
    :func:`download_maf_files`) or an ISA directory (``isa_dir``) holding the
    files.  ``{isa_dir}/{study_id}/`` layout (the downloader's layout) is also
    searched, so ``analyze_maf_files(id, download_root)`` works directly.

    Parameters
    ----------
    study_id : str
        MetaboLights accession (e.g. ``"MTBLS1375"``).
    isa_dir : str | Path | None
        Directory containing the ``m_*.tsv`` files (or a parent of
        ``{study_id}/``).  Ignored when ``maf_paths`` is given.
    maf_paths : list[str | Path] | None
        Explicit MAF file paths to analyze.
    max_examples : int
        How many example names/ids to keep on each result (``MafAnalysis.examples``).

    Returns
    -------
    list[MafAnalysis]
        One result per MAF file found, sorted by filename.  Empty if the study
        has no MAF files / the inputs contain none.
    """
    if not isa_dir and not maf_paths:
        raise ValueError("provide isa_dir or maf_paths")

    targets: list[Path] = []
    if maf_paths:
        targets = [Path(p) for p in maf_paths if p and RE_MAF.match(Path(p).name)]
    elif isa_dir:
        base = Path(isa_dir)
        roots = [base]
        if (base / study_id).is_dir():
            roots.append(base / study_id)
        for root in roots:
            if root.is_dir():
                targets.extend(
                    p for p in root.iterdir()
                    if p.is_file() and RE_MAF.match(p.name)
                )

    if not targets:
        return []
    targets.sort(key=lambda p: p.name)
    return [_analyze_one(study_id, p, max_examples) for p in targets]


def render_maf_summary(analyses: list[MafAnalysis]) -> str:
    """Collapse MAF analyses into one paste-ready text block.

    ``""`` when there are no analyses; otherwise one ``summary`` line per file.
    """
    if not analyses:
        return "No MAF files."
    return "\n".join(a.summary for a in analyses)


def _analyze_one(study_id: str, path: Path, max_examples: int) -> MafAnalysis:
    """Parse + count one MAF file (never raises; errors land in parse_error)."""
    a = MafAnalysis(study_id=study_id, file_name=path.name, file_path=str(path))
    try:
        isa_file, _ = parse_isa_table_sheet_from_fs(str(path))
    except Exception as e:  # noqa: BLE001 - analysis must never crash the agent
        logger.debug("Failed to parse MAF %s: %s", path, e)
        a.parse_error = str(e)
        return a
    if isa_file is None or isa_file.table is None:
        a.parse_error = "table is None"
        return a

    table = isa_file.table
    cols = table.columns or []
    if not cols:
        a.parse_error = "no columns"
        return a

    # ── metabolite count: fullest populated column (real MAFs often leave
    #    the first column empty) ──
    a.metabolite_count = max(len(_values(table, c)) for c in cols)

    # ── sample count: non-metadata columns with any data ──
    for col in cols:
        if _is_metadata_col(col):
            continue
        if not _values(table, col):
            continue
        a.sample_count += 1
        a.sample_columns.append(col)

    # ── annotation depth ──
    name_col = _first_col_matching(table, _NAME_COL_HINTS)
    id_cols = [
        c for c in cols
        if _is_identifier_col(c) and c != name_col
    ]
    mz_col = _first_col_matching(table, ("mass_to_charge", "m/z", "mass"))

    if name_col:
        a.named_count = len(_values(table, name_col))
    if id_cols:
        a.identified_count = max(len(_values(table, c)) for c in id_cols)
    if mz_col:
        a.mz_count = len(_values(table, mz_col))

    if a.named_count > 0:
        a.annotation_level = "named"
    elif a.identified_count > 0:
        a.annotation_level = "identified"
    elif a.mz_count > 0:
        a.annotation_level = "mz_only"
    else:
        a.annotation_level = "empty"

    for cand in (name_col, *(id_cols or []), mz_col):
        if cand and _values(table, cand):
            a.examples = _values(table, cand)[:max_examples]
            break
    return a


# ── helpers ─────────────────────────────────────────────────────────


def _values(table, col: str) -> list[str]:
    """Non-empty cell values of a column, in row order."""
    return [v for v in (table.data.get(col, []) or []) if v and v.strip()]


def _first_col_matching(table, hints: tuple[str, ...]) -> str:
    """First column whose lower-cased name contains any hint."""
    for col in table.columns or []:
        low = col.strip().lower()
        if any(h in low for h in hints):
            return col
    return ""
