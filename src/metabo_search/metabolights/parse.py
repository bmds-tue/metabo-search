"""Parsing for MetaboLights ISA-Tab tables and MAF files.

Handles the tab-separated tables stored under a study directory: sample sheets
(``s_*.txt``), assay files (``a_*.txt``), and MAF files (``m_*.tsv``). The
investigation file (``i_Investigation.txt``) is deliberately not parsed — its
study-level content (protocols, publications, contacts) is already in the
search index.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from metabo_search.metabolights.models import Metabolite, Sample, StudyDetail, StudyFile


@dataclass(frozen=True)
class Table:
    """A parsed tab-separated table.

    Attributes:
        columns: Header names, in file order.
        rows: Row-major records keyed by column name.
    """

    columns: list[str]
    rows: list[dict[str, str]]


def parse_tsv(text: str) -> Table:
    """Parse a tab-separated table.

    The first non-empty line is the header; each remaining non-empty line is a
    row. Missing cells become empty strings; extra cells are ignored.

    Args:
        text: The file contents.

    Returns:
        The parsed table; empty columns/rows for blank input.
    """
    lines = [
        line
        for line in csv.reader(io.StringIO(text), delimiter="\t")
        if any(cell.strip() for cell in line)
    ]
    if not lines:
        return Table(columns=[], rows=[])
    columns = [cell.strip() for cell in lines[0]]
    rows = [
        {columns[i]: (line[i].strip() if i < len(line) else "") for i in range(len(columns))}
        for line in lines[1:]
    ]
    return Table(columns=columns, rows=rows)


# -- sample sheets -----------------------------------------------------


def parse_sample_file(text: str) -> list[Sample]:
    """Parse an ISA sample sheet into per-sample records.

    Organism and organism part are read from the exact
    ``Characteristics[Organism]`` / ``Characteristics[Organism part]`` columns
    (never a substring match, which would put tissue in the organism field).

    Args:
        text: The sample-sheet contents.

    Returns:
        One :class:`Sample` per data row, in file order.
    """
    table = parse_tsv(text)
    if not table.columns:
        return []
    lookup = {column.lower(): column for column in table.columns}
    name_col = lookup.get("sample name")
    if name_col is None:
        return []
    source_col = lookup.get("source name")
    organism_col = lookup.get("characteristics[organism]")
    part_col = lookup.get("characteristics[organism part]")
    type_col = lookup.get("characteristics[sample type]")

    samples: list[Sample] = []
    for row in table.rows:
        name = row.get(name_col, "")
        if not name:
            continue
        samples.append(
            Sample(
                sample_name=name,
                source_name=row.get(source_col, "") if source_col else "",
                organism=row.get(organism_col, "") if organism_col else "",
                organism_part=row.get(part_col, "") if part_col else "",
                sample_type=row.get(type_col, "") if type_col else "",
                characteristics=_bracketed(row, table.columns, "characteristics["),
                factor_values=_bracketed(row, table.columns, "factor value["),
            )
        )
    return samples


def _bracketed(
    row: dict[str, str], columns: list[str], prefix: str
) -> dict[str, str]:
    """Collect ``prefix[label]`` columns into a ``{label: value}`` mapping."""
    out: dict[str, str] = {}
    for column in columns:
        if column.lower().startswith(prefix) and row.get(column):
            out[_bracket_label(column)] = row[column]
    return out


def _bracket_label(column: str) -> str:
    """Extract the label from ``Prefix[label]``."""
    start, end = column.find("["), column.rfind("]")
    if start == -1 or end <= start:
        return column
    return column[start + 1 : end].strip()


# -- assay files -------------------------------------------------------


def parse_assay_file(text: str) -> dict[str, dict[str, list[str]]]:
    """Map sample names to their raw and derived data files in an assay file.

    Args:
        text: The assay-file contents.

    Returns:
        ``sample_name -> {"raw": [...], "derived": [...]}``. Samples with no
        linked file are omitted.
    """
    table = parse_tsv(text)
    lookup = {column.lower(): column for column in table.columns}
    sample_col = lookup.get("sample name")
    raw_col = lookup.get("raw spectral data file")
    derived_col = lookup.get("derived spectral data file")
    if sample_col is None or (raw_col is None and derived_col is None):
        return {}

    out: dict[str, dict[str, list[str]]] = {}
    for row in table.rows:
        name = row.get(sample_col, "")
        if not name:
            continue
        entry = out.setdefault(name, {"raw": [], "derived": []})
        if raw_col and row.get(raw_col):
            entry["raw"].append(row[raw_col])
        if derived_col and row.get(derived_col):
            entry["derived"].append(row[derived_col])
    return out


# -- MAF (metabolite assignment) files ---------------------------------

_METADATA_EXACT = frozenset(
    {
        "database_identifier",
        "chemical_formula",
        "smiles",
        "inchi",
        "inchi_key",
        "name",
        "metabolite_identification",
        "metabolite_name",
        "mass_to_charge",
        "fragmentation",
        "modifications",
        "charge",
        "retention_time",
        "retention_index",
        "taxid",
        "species",
        "database",
        "database_version",
        "reliability",
        "uri",
        "search_engine",
        "search_engine_score",
        "annotation_confidence",
        "qc",
        "comment",
        "notes",
    }
)

_METADATA_SUFFIXES = ("_identifier", "_sub", "_stdev", "_std_error", "_mean", "_median")

_NAME_COLUMNS = ("metabolite_identification", "metabolite_name", "compound_name", "name")


def is_metadata_column(column: str) -> bool:
    """True if a MAF column is metadata/summary, not a per-sample abundance."""
    name = column.strip().lower()
    if name in _METADATA_EXACT or "abundance" in name:
        return True
    return any(name.endswith(suffix) for suffix in _METADATA_SUFFIXES)


def parse_maf(study_id: str, file_name: str, text: str) -> list[Metabolite]:
    """Parse a MAF file into metabolite rows.

    Non-metadata columns are treated as per-sample abundances. The metabolite
    name is read from ``metabolite_identification`` when present (real MAFs
    often leave ``database_identifier`` empty).

    Args:
        study_id: Owning study accession.
        file_name: MAF file name (stored on each row).
        text: The MAF contents.

    Returns:
        One :class:`Metabolite` per data row, in file order.
    """
    table = parse_tsv(text)
    if not table.columns:
        return []
    lookup = {column.lower(): column for column in table.columns}
    name_col = next((lookup[key] for key in _NAME_COLUMNS if key in lookup), None)
    db_col = lookup.get("database_identifier")
    formula_col = lookup.get("chemical_formula")
    mz_col = lookup.get("mass_to_charge")
    rt_col = lookup.get("retention_time")
    sample_columns = [column for column in table.columns if not is_metadata_column(column)]

    metabolites: list[Metabolite] = []
    for index, row in enumerate(table.rows):
        abundances: dict[str, float] = {}
        for column in sample_columns:
            value = _to_float(row.get(column, ""))
            if value is not None:
                abundances[column] = value
        metabolites.append(
            Metabolite(
                study_id=study_id,
                file=file_name,
                row=index,
                database_identifier=row.get(db_col, "") if db_col else "",
                metabolite_identification=row.get(name_col, "") if name_col else "",
                chemical_formula=row.get(formula_col, "") if formula_col else "",
                mass_to_charge=_to_float(row.get(mz_col, "")) if mz_col else None,
                retention_time=_to_float(row.get(rt_col, "")) if rt_col else None,
                abundances=abundances,
            )
        )
    return metabolites


def _to_float(value: str) -> float | None:
    """Parse a float, returning ``None`` for blank or non-numeric values."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# -- study detail assembly --------------------------------------------


def parse_study_detail(
    study_id: str,
    isa_files: Mapping[str, str],
    *,
    files: Sequence[StudyFile] = (),
) -> StudyDetail:
    """Assemble a :class:`StudyDetail` from a study's ISA-Tab files.

    Parses every sample sheet (``s_*.txt``), assay file (``a_*.txt``), and MAF
    (``m_*.tsv``). Each sample's ``raw_files``/``derived_files`` come from the
    assay files' sample-to-file columns.

    Args:
        study_id: Owning study accession.
        isa_files: File name to contents, as returned by the client.
        files: Optional pre-listed ``FILES/`` records to attach.

    Returns:
        The assembled study detail.
    """
    file_map: dict[str, dict[str, list[str]]] = {}
    samples: list[Sample] = []
    metabolites: list[Metabolite] = []

    for name, text in isa_files.items():
        prefix = name[:2].lower()
        if prefix == "s_":
            samples.extend(parse_sample_file(text))
        elif prefix == "a_":
            for sample_name, entry in parse_assay_file(text).items():
                target = file_map.setdefault(sample_name, {"raw": [], "derived": []})
                target["raw"].extend(entry["raw"])
                target["derived"].extend(entry["derived"])
        elif prefix == "m_":
            metabolites.extend(parse_maf(study_id, name, text))

    linked = [
        sample.model_copy(
            update={
                "raw_files": file_map.get(sample.sample_name, {}).get("raw", []),
                "derived_files": file_map.get(sample.sample_name, {}).get("derived", []),
            }
        )
        for sample in samples
    ]
    return StudyDetail(
        study_id=study_id, samples=linked, metabolites=metabolites, files=list(files)
    )
