"""Tests for ISA-Tab and MAF parsing (offline, inline real-world shapes)."""

from __future__ import annotations

from metabo_search.metabolights.parse import (
    is_metadata_column,
    parse_assay_file,
    parse_maf,
    parse_sample_file,
    parse_study_detail,
    parse_tsv,
)

SAMPLE_SHEET = "\n".join(
    [
        "Sample Name\tSource Name\tCharacteristics[Organism]\t"
        "Characteristics[Organism part]\tCharacteristics[Sample type]\t"
        "Factor Value[Gender]\tFactor Value[Disease]",
        "DCR00004_U\tHuman1\tHomo sapiens\turine\texperimental sample\t"
        "Female\tAlzheimer's disease",
        "DCR00005_U\tHuman2\tHomo sapiens\turine\texperimental sample\tMale\tControl",
    ]
)

ASSAY_FILE = "\n".join(
    [
        "Sample Name\tRaw Spectral Data File\tDerived Spectral Data File",
        "DCR00004_U\tFILES/RAW_FILES/DCR00004_U.raw\t"
        "FILES/DERIVED_FILES/DCR00004_U.mzML",
        "DCR00005_U\tFILES/RAW_FILES/DCR00005_U.raw\t"
        "FILES/DERIVED_FILES/DCR00005_U.mzML",
    ]
)

MAF = "\n".join(
    [
        "database_identifier\tchemical_formula\tmetabolite_identification\t"
        "mass_to_charge\tretention_time\t5_NIST_A-1\t6_NIST_A-2",
        "\tC27H46O\tcholesterol\t386.35\t10.5\t1234.5\t2345.6",
        "\tC6H12O6\tglucose\t180.06\t1.2\t100.0\t200.0",
    ]
)


def test_parse_tsv_handles_ragged_rows() -> None:
    table = parse_tsv("a\tb\tc\n1\t2\n3\t4\t5\t6\n")
    assert table.columns == ["a", "b", "c"]
    assert table.rows == [{"a": "1", "b": "2", "c": ""}, {"a": "3", "b": "4", "c": "5"}]


def test_parse_tsv_blank_input() -> None:
    table = parse_tsv("\n\n")
    assert table.columns == []
    assert table.rows == []


def test_parse_sample_file_organism_is_not_tissue() -> None:
    samples = parse_sample_file(SAMPLE_SHEET)
    assert [s.sample_name for s in samples] == ["DCR00004_U", "DCR00005_U"]
    first = samples[0]
    assert first.organism == "Homo sapiens"
    assert first.organism_part == "urine"
    assert first.source_name == "Human1"


def test_parse_sample_file_characteristics_and_factors() -> None:
    first = parse_sample_file(SAMPLE_SHEET)[0]
    assert first.characteristics["Organism part"] == "urine"
    assert first.characteristics["Sample type"] == "experimental sample"
    assert first.factor_values == {"Gender": "Female", "Disease": "Alzheimer's disease"}


def test_parse_assay_file_maps_samples_to_files() -> None:
    mapping = parse_assay_file(ASSAY_FILE)
    assert mapping["DCR00004_U"]["raw"] == ["FILES/RAW_FILES/DCR00004_U.raw"]
    assert mapping["DCR00004_U"]["derived"] == ["FILES/DERIVED_FILES/DCR00004_U.mzML"]


def test_parse_assay_file_without_data_columns() -> None:
    assert parse_assay_file("Sample Name\tOther\nA\tx\n") == {}


def test_metadata_column_classification() -> None:
    assert is_metadata_column("database_identifier")
    assert is_metadata_column("mass_to_charge")
    assert is_metadata_column("SwissLipid_identifier")
    assert is_metadata_column("abundance_stdev_sub")
    assert not is_metadata_column("5_NIST_A-1")
    assert not is_metadata_column("MTBLS1375_A_01")


def test_parse_maf_prefers_identification_name() -> None:
    metabolites = parse_maf("MTBLS1", "m_MTBLS1_maf.tsv", MAF)
    assert [m.metabolite_identification for m in metabolites] == ["cholesterol", "glucose"]
    assert metabolites[0].chemical_formula == "C27H46O"
    assert metabolites[0].mass_to_charge == 386.35
    assert metabolites[0].retention_time == 10.5


def test_parse_maf_abundances_exclude_metadata() -> None:
    first = parse_maf("MTBLS1", "m_MTBLS1_maf.tsv", MAF)[0]
    assert first.abundances == {"5_NIST_A-1": 1234.5, "6_NIST_A-2": 2345.6}


def test_parse_study_detail_links_samples_to_files() -> None:
    isa = {
        "s_MTBLS1.txt": SAMPLE_SHEET,
        "a_MTBLS1.txt": ASSAY_FILE,
        "m_MTBLS1_maf.tsv": MAF,
    }
    detail = parse_study_detail("MTBLS1", isa)
    assert detail.study_id == "MTBLS1"
    assert [s.sample_name for s in detail.samples] == ["DCR00004_U", "DCR00005_U"]
    assert detail.samples[0].raw_files == ["FILES/RAW_FILES/DCR00004_U.raw"]
    assert detail.samples[0].derived_files == ["FILES/DERIVED_FILES/DCR00004_U.mzML"]
    assert len(detail.metabolites) == 2
