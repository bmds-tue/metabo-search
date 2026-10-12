"""MetaboLights models → DuckDB rows.

Two pieces, deliberately separate:

* :func:`batch` is a **pure** mapping from MetaboLights models to an
  :class:`~metabo_search.workspace.IngestBatch`. No database, no network.
* :func:`write` applies that batch to a :class:`Workspace` and records an
  audit row. Sources depend on the workspace, never the reverse.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from metabo_search.metabolights.models import (
    Assay,
    AssayTechnique,
    Contact,
    FactorPair,
    Funding,
    Metabolite,
    OntologyTerm,
    OrganismLineage,
    Protocol,
    ProtocolComponent,
    ProtocolParameter,
    Publication,
    Sample,
    StudyDetail,
    StudyFile,
    StudyHit,
    Submitter,
)
from metabo_search.workspace import IngestBatch, IngestReport, TableRows, Workspace

SOURCE = "metabolights"
"""Value recorded in the ``source`` column of the audit table."""

_AUDIT_TABLE = "ml_ingest_run"

# Table name -> (column name -> SQL type, natural key). The DDL is generated
# from this, so the schema and the primary keys cannot drift apart.
_TABLES: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "ml_study": (
        {
            "study_id": "VARCHAR",
            "title": "VARCHAR",
            "description": "VARCHAR",
            "status": "VARCHAR",
            "study_category": "VARCHAR",
            "curation_request": "VARCHAR",
            "submission_date": "VARCHAR",
            "public_release_date": "VARCHAR",
            "modified_time": "VARCHAR",
            "sample_count": "INTEGER",
            "assay_count": "INTEGER",
            "raw_file_count": "INTEGER",
            "derived_file_count": "INTEGER",
            "size_in_bytes": "BIGINT",
            "size_in_text": "VARCHAR",
            "investigation_file_path": "VARCHAR",
            "sample_file_path": "VARCHAR",
        },
        ("study_id",),
    ),
    "ml_study_term": (
        {
            "study_id": "VARCHAR",
            "facet": "VARCHAR",
            "term": "VARCHAR",
            "source_ref": "VARCHAR",
            "accession": "VARCHAR",
        },
        ("study_id", "facet", "term"),
    ),
    "ml_study_lineage": (
        {"study_id": "VARCHAR", "rank": "VARCHAR", "term": "VARCHAR"},
        ("study_id", "rank", "term"),
    ),
    "ml_study_factor_pair": (
        {"study_id": "VARCHAR", "name": "VARCHAR", "value": "VARCHAR"},
        ("study_id", "name", "value"),
    ),
    "ml_study_annotation": (
        {
            "study_id": "VARCHAR",
            "kind": "VARCHAR",
            "ordinal": "INTEGER",
            "value": "VARCHAR",
        },
        ("study_id", "kind", "ordinal"),
    ),
    "ml_study_extension": (
        {"study_id": "VARCHAR", "kind": "VARCHAR", "extension": "VARCHAR"},
        ("study_id", "kind", "extension"),
    ),
    "ml_assay": (
        {
            "study_id": "VARCHAR",
            "assay_index": "INTEGER",
            "file_path": "VARCHAR",
            "technology_platform": "VARCHAR",
            "measurement_type_term": "VARCHAR",
            "measurement_type_source_ref": "VARCHAR",
            "measurement_type_accession": "VARCHAR",
            "technology_type_term": "VARCHAR",
            "technology_type_source_ref": "VARCHAR",
            "technology_type_accession": "VARCHAR",
            "technique_name": "VARCHAR",
            "technique_main": "VARCHAR",
            "technique_sub": "VARCHAR",
            "technique": "VARCHAR",
        },
        ("study_id", "assay_index"),
    ),
    "ml_assay_file": (
        {"study_id": "VARCHAR", "assay_index": "INTEGER", "path": "VARCHAR"},
        ("study_id", "assay_index", "path"),
    ),
    "ml_assay_technique": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "name": "VARCHAR",
            "main": "VARCHAR",
            "sub": "VARCHAR",
            "technique": "VARCHAR",
        },
        ("study_id", "ordinal", "name"),
    ),
    "ml_publication": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "title": "VARCHAR",
            "author_list": "VARCHAR",
            "pubmed_id": "VARCHAR",
            "doi": "VARCHAR",
            "status_term": "VARCHAR",
            "status_source_ref": "VARCHAR",
            "status_accession": "VARCHAR",
        },
        ("study_id", "ordinal", "doi"),
    ),
    "ml_protocol": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "name": "VARCHAR",
            "description": "VARCHAR",
            "uri": "VARCHAR",
            "version": "VARCHAR",
            "protocol_type_term": "VARCHAR",
            "protocol_type_source_ref": "VARCHAR",
            "protocol_type_accession": "VARCHAR",
        },
        ("study_id", "ordinal"),
    ),
    "ml_protocol_component": (
        {
            "study_id": "VARCHAR",
            "protocol_ordinal": "INTEGER",
            "ordinal": "INTEGER",
            "name": "VARCHAR",
            "type": "VARCHAR",
            "term_source_ref": "VARCHAR",
            "term_accession_number": "VARCHAR",
        },
        ("study_id", "protocol_ordinal", "ordinal"),
    ),
    "ml_protocol_parameter": (
        {
            "study_id": "VARCHAR",
            "protocol_ordinal": "INTEGER",
            "ordinal": "INTEGER",
            "term": "VARCHAR",
            "term_source_ref": "VARCHAR",
            "term_accession_number": "VARCHAR",
        },
        ("study_id", "protocol_ordinal", "ordinal"),
    ),
    "ml_contact": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "first_name": "VARCHAR",
            "last_name": "VARCHAR",
            "mid_initials": "VARCHAR",
            "email": "VARCHAR",
            "affiliation": "VARCHAR",
            "address": "VARCHAR",
            "orcid": "VARCHAR",
        },
        ("study_id", "ordinal"),
    ),
    "ml_contact_role": (
        {
            "study_id": "VARCHAR",
            "contact_ordinal": "INTEGER",
            "term": "VARCHAR",
            "source_ref": "VARCHAR",
            "accession": "VARCHAR",
        },
        ("study_id", "contact_ordinal", "term"),
    ),
    "ml_submitter": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "fullname": "VARCHAR",
            "email": "VARCHAR",
            "affiliation": "VARCHAR",
            "address": "VARCHAR",
            "country": "VARCHAR",
            "orcid": "VARCHAR",
        },
        ("study_id", "ordinal"),
    ),
    "ml_submitter_role": (
        {"study_id": "VARCHAR", "submitter_ordinal": "INTEGER", "role": "VARCHAR"},
        ("study_id", "submitter_ordinal", "role"),
    ),
    "ml_funding": (
        {
            "study_id": "VARCHAR",
            "ordinal": "INTEGER",
            "fund_ref_id": "VARCHAR",
            "funder": "VARCHAR",
            "grant_identifier": "VARCHAR",
        },
        ("study_id", "ordinal"),
    ),
    "ml_sample": (
        {
            "study_id": "VARCHAR",
            "sample_name": "VARCHAR",
            "source_name": "VARCHAR",
            "organism": "VARCHAR",
            "organism_part": "VARCHAR",
            "sample_type": "VARCHAR",
        },
        ("study_id", "sample_name"),
    ),
    "ml_sample_characteristic": (
        {
            "study_id": "VARCHAR",
            "sample_name": "VARCHAR",
            "key": "VARCHAR",
            "value": "VARCHAR",
        },
        ("study_id", "sample_name", "key"),
    ),
    "ml_sample_factor": (
        {
            "study_id": "VARCHAR",
            "sample_name": "VARCHAR",
            "name": "VARCHAR",
            "value": "VARCHAR",
        },
        ("study_id", "sample_name", "name"),
    ),
    "ml_sample_file": (
        {
            "study_id": "VARCHAR",
            "sample_name": "VARCHAR",
            "kind": "VARCHAR",
            "path": "VARCHAR",
        },
        ("study_id", "sample_name", "kind", "path"),
    ),
    "ml_metabolite": (
        {
            "study_id": "VARCHAR",
            "file": "VARCHAR",
            "row": "INTEGER",
            "database_identifier": "VARCHAR",
            "metabolite_identification": "VARCHAR",
            "chemical_formula": "VARCHAR",
            "mass_to_charge": "DOUBLE",
            "retention_time": "DOUBLE",
        },
        ("study_id", "file", "row"),
    ),
    "ml_metabolite_abundance": (
        {
            "study_id": "VARCHAR",
            "file": "VARCHAR",
            "row": "INTEGER",
            "sample": "VARCHAR",
            "value": "DOUBLE",
        },
        ("study_id", "file", "row", "sample"),
    ),
    "ml_file": (
        {
            "study_id": "VARCHAR",
            "path": "VARCHAR",
            "name": "VARCHAR",
            "directory": "VARCHAR",
            "extension": "VARCHAR",
            "size_bytes": "BIGINT",
            "category": "VARCHAR",
        },
        ("study_id", "path"),
    ),
    _AUDIT_TABLE: (
        {
            "run_id": "VARCHAR",
            "table_name": "VARCHAR",
            "source": "VARCHAR",
            "rows": "BIGINT",
            "at": "VARCHAR",
        },
        ("run_id", "table_name"),
    ),
}


def _build_ddl(tables: dict[str, tuple[dict[str, str], tuple[str, ...]]]) -> str:
    """Render ``CREATE TABLE IF NOT EXISTS`` statements from the table spec."""
    statements: list[str] = []
    for name, (columns, keys) in tables.items():
        column_defs = ", ".join(
            f'"{column}" {sql_type}' for column, sql_type in columns.items()
        )
        key_defs = ", ".join(f'"{key}"' for key in keys)
        statements.append(
            f'CREATE TABLE IF NOT EXISTS "{name}" ({column_defs}, '
            f"PRIMARY KEY ({key_defs}))"
        )
    return ";\n".join(statements) + ";"


DDL = _build_ddl(_TABLES)
"""DDL for every table this source writes (idempotent ``CREATE IF NOT EXISTS``)."""

# Fields that do not land in a same-named column: they expand into child
# tables, flatten into prefixed columns, or are deliberately not persisted.
# The completeness test asserts these cover every remaining model field, so a
# new upstream field fails the test instead of being silently dropped.
_EXPANDED: dict[type[object], set[str]] = {
    StudyHit: {
        "organisms",
        "organism_parts",
        "sample_types",
        "technology_types",
        "design_descriptors",
        "factors",
        "variants",
        "annotations",
        "curator_annotations",
        "parse_errors",
        "organism_lineage",
        "raw_file_extensions",
        "derived_file_extensions",
        "assays",
        "assay_techniques",
        "factor_pairs",
        "publications",
        "protocols",
        "contacts",
        "submitters",
        "fundings",
    },
    OntologyTerm: set(),
    AssayTechnique: set(),
    Assay: {"measurement_type", "technology_type", "technique", "assignment_files"},
    FactorPair: set(),
    Publication: {"status"},
    ProtocolComponent: set(),
    ProtocolParameter: set(),
    Protocol: {"protocol_type", "components", "parameters"},
    Contact: {"roles"},
    Submitter: {"roles"},
    Funding: set(),
    OrganismLineage: {
        "domain",
        "kingdom",
        "phylum",
        "klass",
        "order",
        "family",
        "genus",
        "species",
    },
    Sample: {"characteristics", "factor_values", "raw_files", "derived_files"},
    Metabolite: {"abundances"},
    StudyFile: set(),
    StudyDetail: {"samples", "metabolites", "files", "error"},
}
"""Per-model fields not stored under a same-named column (see the note above)."""


def batch(obj: object) -> IngestBatch:
    """Map MetaboLights models to an ingestion batch. Pure: no DB, no network.

    Args:
        obj: A single model, or a list/``Results`` of models. Accepted types:
            :class:`StudyHit`, :class:`StudyDetail`, and :class:`StudyFile`.

    Returns:
        One :class:`IngestBatch` with the source DDL and a table entry per
        ``ml_*`` table (empty tables are skipped by ``Workspace.write``).

    Raises:
        TypeError: An item is not an ingestible MetaboLights model.
    """
    items: list[object] = cast("list[object]", obj) if isinstance(obj, list) else [obj]
    tables: dict[str, list[dict[str, object]]] = {name: [] for name in _TABLES}
    for item in items:
        _map(item, tables)
    return IngestBatch(ddl=DDL, tables=_to_table_rows(tables))


def write(ws: Workspace, obj: object) -> IngestReport:
    """Write models to ``ws`` idempotently and record an audit row.

    Args:
        ws: The workspace to write to.
        obj: Same as :func:`batch`.

    Returns:
        The per-table row counts, plus the ids of any errored ``StudyDetail``
        objects that were skipped (their data is empty by construction).
    """
    skipped = _skipped(obj)
    report = ws.write(batch(obj))
    _record_run(ws, report)
    return replace(report, skipped=skipped)


def _skipped(obj: object) -> list[str]:
    """Study ids of errored ``StudyDetail`` items (best-effort inspect)."""
    items: list[object] = cast("list[object]", obj) if isinstance(obj, list) else [obj]
    return [
        item.study_id
        for item in items
        if isinstance(item, StudyDetail) and item.error
    ]


def _record_run(ws: Workspace, report: IngestReport) -> None:
    """Append one audit row per written table for this ingestion call."""
    if not report.tables:
        return
    columns, keys = _TABLES[_AUDIT_TABLE]
    run_id = uuid4().hex
    at = datetime.now(UTC).isoformat()
    rows: list[dict[str, object]] = [
        {
            "run_id": run_id,
            "table_name": name,
            "source": SOURCE,
            "rows": count,
            "at": at,
        }
        for name, count in report.tables.items()
    ]
    ws.write(
        IngestBatch(
            ddl=DDL,
            tables=[
                TableRows(
                    name=_AUDIT_TABLE,
                    columns=tuple(columns),
                    rows=rows,
                    keys=keys,
                )
            ],
        )
    )


def _to_table_rows(
    tables: dict[str, list[dict[str, object]]],
) -> list[TableRows]:
    """Wrap each table's rows with its declared columns and natural key."""
    return [
        TableRows(
            name=name,
            columns=tuple(columns),
            rows=tables[name],
            keys=keys,
        )
        for name, (columns, keys) in _TABLES.items()
    ]


def _add(
    tables: dict[str, list[dict[str, object]]],
    table: str,
    row: dict[str, object],
) -> None:
    """Append one row to a table's pending rows."""
    tables[table].append(row)


def _map(item: object, tables: dict[str, list[dict[str, object]]]) -> None:
    """Dispatch one model to its mapping function."""
    if isinstance(item, StudyHit):
        _study_hit(item, tables)
    elif isinstance(item, StudyDetail):
        # Best-effort inspect: an errored detail has no data to write.
        if not item.error:
            _study_detail(item, tables)
    elif isinstance(item, StudyFile):
        _study_file(item.study_id, item, tables)
    else:
        raise TypeError(f"cannot ingest {type(item).__name__} into MetaboLights tables")


def _study_hit(hit: StudyHit, tables: dict[str, list[dict[str, object]]]) -> None:
    """Map a shallow study document, including its nested collections."""
    _add(
        tables,
        "ml_study",
        {
            "study_id": hit.study_id,
            "title": hit.title,
            "description": hit.description,
            "status": hit.status,
            "study_category": hit.study_category,
            "curation_request": hit.curation_request,
            "submission_date": hit.submission_date,
            "public_release_date": hit.public_release_date,
            "modified_time": hit.modified_time,
            "sample_count": hit.sample_count,
            "assay_count": hit.assay_count,
            "raw_file_count": hit.raw_file_count,
            "derived_file_count": hit.derived_file_count,
            "size_in_bytes": hit.size_in_bytes,
            "size_in_text": hit.size_in_text,
            "investigation_file_path": hit.investigation_file_path,
            "sample_file_path": hit.sample_file_path,
        },
    )
    for facet, terms in (
        ("organism", hit.organisms),
        ("organism_part", hit.organism_parts),
        ("sample_type", hit.sample_types),
        ("technology_type", hit.technology_types),
        ("design_descriptor", hit.design_descriptors),
        ("factor", hit.factors),
        ("variant", hit.variants),
    ):
        for term in terms:
            _add(tables, "ml_study_term", _term_row(hit.study_id, facet, term))
    for rank, terms in (
        ("domain", hit.organism_lineage.domain),
        ("kingdom", hit.organism_lineage.kingdom),
        ("phylum", hit.organism_lineage.phylum),
        ("klass", hit.organism_lineage.klass),
        ("order", hit.organism_lineage.order),
        ("family", hit.organism_lineage.family),
        ("genus", hit.organism_lineage.genus),
        ("species", hit.organism_lineage.species),
    ):
        for term in terms:
            _add(
                tables,
                "ml_study_lineage",
                {"study_id": hit.study_id, "rank": rank, "term": term},
            )
    for pair in hit.factor_pairs:
        _add(
            tables,
            "ml_study_factor_pair",
            {"study_id": hit.study_id, "name": pair.name, "value": pair.value},
        )
    for kind, values in (
        ("annotation", hit.annotations),
        ("curator", hit.curator_annotations),
        ("parse_error", hit.parse_errors),
    ):
        for ordinal, value in enumerate(values):
            _add(
                tables,
                "ml_study_annotation",
                {
                    "study_id": hit.study_id,
                    "kind": kind,
                    "ordinal": ordinal,
                    "value": value,
                },
            )
    for kind, extensions in (
        ("raw", hit.raw_file_extensions),
        ("derived", hit.derived_file_extensions),
    ):
        for extension in extensions:
            _add(
                tables,
                "ml_study_extension",
                {"study_id": hit.study_id, "kind": kind, "extension": extension},
            )
    for index, assay in enumerate(hit.assays):
        _assay(hit.study_id, index, assay, tables)
    for ordinal, technique in enumerate(hit.assay_techniques):
        _add(
            tables,
            "ml_assay_technique",
            {
                "study_id": hit.study_id,
                "ordinal": ordinal,
                "name": technique.name,
                "main": technique.main,
                "sub": technique.sub,
                "technique": technique.technique,
            },
        )
    for ordinal, publication in enumerate(hit.publications):
        _publication(hit.study_id, ordinal, publication, tables)
    for ordinal, protocol in enumerate(hit.protocols):
        _protocol(hit.study_id, ordinal, protocol, tables)
    for ordinal, contact in enumerate(hit.contacts):
        _contact(hit.study_id, ordinal, contact, tables)
    for ordinal, submitter in enumerate(hit.submitters):
        _submitter(hit.study_id, ordinal, submitter, tables)
    for ordinal, funding in enumerate(hit.fundings):
        _add(
            tables,
            "ml_funding",
            {
                "study_id": hit.study_id,
                "ordinal": ordinal,
                "fund_ref_id": funding.fund_ref_id,
                "funder": funding.funder,
                "grant_identifier": funding.grant_identifier,
            },
        )


def _study_detail(
    detail: StudyDetail, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map deep per-study data: samples, metabolites, and listed files."""
    for sample in detail.samples:
        _sample(detail.study_id, sample, tables)
    for metabolite in detail.metabolites:
        _metabolite(detail.study_id, metabolite, tables)
    for file in detail.files:
        _study_file(detail.study_id, file, tables)


def _study_file(
    study_id: str, file: StudyFile, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map one ``FILES/`` entry. The owning study id comes from the caller."""
    _add(
        tables,
        "ml_file",
        {
            "study_id": study_id,
            "path": file.path,
            "name": file.name,
            "directory": file.directory,
            "extension": file.extension,
            "size_bytes": file.size_bytes,
            "category": file.category,
        },
    )


def _term_row(study_id: str, facet: str, term: OntologyTerm) -> dict[str, object]:
    """Flatten an ontology term into a ``ml_study_term`` row."""
    return {
        "study_id": study_id,
        "facet": facet,
        "term": term.term,
        "source_ref": term.term_source_ref,
        "accession": term.term_accession_number,
    }


def _assay(
    study_id: str,
    index: int,
    assay: Assay,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    """Map one assay, flattening its two ontology terms and its technique."""
    measurement, technology, technique = (
        assay.measurement_type,
        assay.technology_type,
        assay.technique,
    )
    _add(
        tables,
        "ml_assay",
        {
            "study_id": study_id,
            "assay_index": index,
            "file_path": assay.file_path,
            "technology_platform": assay.technology_platform,
            "measurement_type_term": measurement.term,
            "measurement_type_source_ref": measurement.term_source_ref,
            "measurement_type_accession": measurement.term_accession_number,
            "technology_type_term": technology.term,
            "technology_type_source_ref": technology.term_source_ref,
            "technology_type_accession": technology.term_accession_number,
            "technique_name": technique.name,
            "technique_main": technique.main,
            "technique_sub": technique.sub,
            "technique": technique.technique,
        },
    )
    for path in assay.assignment_files:
        _add(
            tables,
            "ml_assay_file",
            {"study_id": study_id, "assay_index": index, "path": path},
        )


def _publication(
    study_id: str,
    ordinal: int,
    publication: Publication,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    """Map one publication, flattening its status ontology term."""
    status = publication.status
    _add(
        tables,
        "ml_publication",
        {
            "study_id": study_id,
            "ordinal": ordinal,
            "title": publication.title,
            "author_list": publication.author_list,
            "pubmed_id": publication.pubmed_id,
            "doi": publication.doi,
            "status_term": status.term,
            "status_source_ref": status.term_source_ref,
            "status_accession": status.term_accession_number,
        },
    )


def _protocol(
    study_id: str,
    ordinal: int,
    protocol: Protocol,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    """Map one protocol plus its components and parameters."""
    protocol_type = protocol.protocol_type
    _add(
        tables,
        "ml_protocol",
        {
            "study_id": study_id,
            "ordinal": ordinal,
            "name": protocol.name,
            "description": protocol.description,
            "uri": protocol.uri,
            "version": protocol.version,
            "protocol_type_term": protocol_type.term,
            "protocol_type_source_ref": protocol_type.term_source_ref,
            "protocol_type_accession": protocol_type.term_accession_number,
        },
    )
    for component_ordinal, component in enumerate(protocol.components):
        _add(
            tables,
            "ml_protocol_component",
            {
                "study_id": study_id,
                "protocol_ordinal": ordinal,
                "ordinal": component_ordinal,
                "name": component.name,
                "type": component.type,
                "term_source_ref": component.term_source_ref,
                "term_accession_number": component.term_accession_number,
            },
        )
    for parameter_ordinal, parameter in enumerate(protocol.parameters):
        _add(
            tables,
            "ml_protocol_parameter",
            {
                "study_id": study_id,
                "protocol_ordinal": ordinal,
                "ordinal": parameter_ordinal,
                "term": parameter.term,
                "term_source_ref": parameter.term_source_ref,
                "term_accession_number": parameter.term_accession_number,
            },
        )


def _contact(
    study_id: str,
    ordinal: int,
    contact: Contact,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    """Map one contact and its role ontology terms."""
    _add(
        tables,
        "ml_contact",
        {
            "study_id": study_id,
            "ordinal": ordinal,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "mid_initials": contact.mid_initials,
            "email": contact.email,
            "affiliation": contact.affiliation,
            "address": contact.address,
            "orcid": contact.orcid,
        },
    )
    for role in contact.roles:
        _add(
            tables,
            "ml_contact_role",
            {
                "study_id": study_id,
                "contact_ordinal": ordinal,
                "term": role.term,
                "source_ref": role.term_source_ref,
                "accession": role.term_accession_number,
            },
        )


def _submitter(
    study_id: str,
    ordinal: int,
    submitter: Submitter,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    """Map one submitter and its plain-string roles."""
    _add(
        tables,
        "ml_submitter",
        {
            "study_id": study_id,
            "ordinal": ordinal,
            "fullname": submitter.fullname,
            "email": submitter.email,
            "affiliation": submitter.affiliation,
            "address": submitter.address,
            "country": submitter.country,
            "orcid": submitter.orcid,
        },
    )
    for role in submitter.roles:
        _add(
            tables,
            "ml_submitter_role",
            {"study_id": study_id, "submitter_ordinal": ordinal, "role": role},
        )


def _sample(
    study_id: str, sample: Sample, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map one sample, exploding its characteristics and files into rows."""
    _add(
        tables,
        "ml_sample",
        {
            "study_id": study_id,
            "sample_name": sample.sample_name,
            "source_name": sample.source_name,
            "organism": sample.organism,
            "organism_part": sample.organism_part,
            "sample_type": sample.sample_type,
        },
    )
    for key, value in sample.characteristics.items():
        _add(
            tables,
            "ml_sample_characteristic",
            {"study_id": study_id, "sample_name": sample.sample_name, "key": key, "value": value},
        )
    for name, value in sample.factor_values.items():
        _add(
            tables,
            "ml_sample_factor",
            {"study_id": study_id, "sample_name": sample.sample_name, "name": name, "value": value},
        )
    for kind, paths in (("raw", sample.raw_files), ("derived", sample.derived_files)):
        for path in paths:
            _add(
                tables,
                "ml_sample_file",
                {
                    "study_id": study_id,
                    "sample_name": sample.sample_name,
                    "kind": kind,
                    "path": path,
                },
            )


def _metabolite(
    study_id: str, metabolite: Metabolite, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map one MAF row and its per-sample abundances."""
    _add(
        tables,
        "ml_metabolite",
        {
            "study_id": study_id,
            "file": metabolite.file,
            "row": metabolite.row,
            "database_identifier": metabolite.database_identifier,
            "metabolite_identification": metabolite.metabolite_identification,
            "chemical_formula": metabolite.chemical_formula,
            "mass_to_charge": metabolite.mass_to_charge,
            "retention_time": metabolite.retention_time,
        },
    )
    for sample, value in metabolite.abundances.items():
        _add(
            tables,
            "ml_metabolite_abundance",
            {
                "study_id": study_id,
                "file": metabolite.file,
                "row": metabolite.row,
                "sample": sample,
                "value": value,
            },
        )
