---
name: metabo-search
description: Search MetaboLights and Metabolomics Workbench for metabolomics datasets from Python. Two independent, strictly typed clients — study search, point lookups, deep ISA/MAF inspection (MetaboLights), factors/analysis/metabolites (Workbench), and selective downloads. Use when a researcher needs datasets matching an experimental setup, or wants to pull and analyse study data.
compatibility: Python 3.11+; uv optional (falls back to python3 -m venv)
metadata:
  install: scripts/install.sh
  package: metabo_search
---

# metabo-search

Search two metabolomics repositories from Python. They are **not unified**:
MetaboLights and Metabolomics Workbench have different APIs, vocabularies, and
data models, and this library keeps them separate.

## Setup (run once, from the skill root)

```bash
./scripts/install.sh       # creates .venv-local, installs the package, links the skill
```

Use the venv python wrapper (portable, no hardcoded paths):

```bash
scripts/python -c "import metabo_search; print(metabo_search.__file__)"
```

> Never use the system/homebrew python. If `import metabo_search` resolves
> elsewhere, re-run `./scripts/install.sh`. Uninstall: `scripts/uninstall.sh`.

## MetaboLights

```python
from metabo_search import MetaboLights

ml = MetaboLights()

# Search the public study index. Facet filters run server-side; min_samples /
# min_raw_files are applied client-side (the API has no numeric filter).
hits = ml.search(
    "human blood plasma lipidomics",
    organisms=["Homo sapiens"],     # facet: organisms.term
    sample_types=["blood plasma"],  # facet: organismParts.term
    techniques=["LC-MS"],           # facet: assayTechniques.name
    min_samples=50,
    limit=25,
)

ml.study("MTBLS1")                  # one StudyHit
ml.studies(["MTBLS2", "MTBLS1"])    # input order preserved

# Deep data: download and parse ISA samples + MAF metabolites.
detail = ml.inspect(["MTBLS1"])[0]
detail.samples[0].organism          # "Homo sapiens"
detail.samples[0].organism_part     # "urine"
detail.metabolites[0].metabolite_identification
# inspect is best-effort: a study that failed after retries comes back with
# .error set (and empty data) instead of aborting the batch. Pass
# on_error="raise" to abort on the first failure.

ml.files("MTBLS1")                                   # recursive FILES/ listing
ml.download("MTBLS1", "derived", dest="data/", max_files=10)
```

MetaboLights vocabulary matters: use the index's facet values (`"blood plasma"`,
`"blood serum"`), not free variants.

## Metabolomics Workbench

```python
from metabo_search import Workbench

wb = Workbench()

# The Workbench has no server-side free-text search: the whole-index summary
# corpus is fetched once per client (~90 s) and filtered locally.
wb.search("diabetes urine", limit=25)

wb.study("ST000001")
wb.samples("ST000001")       # parsed factor rows
wb.analyses("ST000001")      # MS/NMR analysis records
wb.metabolites("ST000001")   # empty means "not listed", not necessarily "none"
wb.inspect(["ST000001"])     # bundles summary + samples + analyses + metabolites
```

Workbench vocabulary differs: `species` (Latin name), `source` (e.g. `Blood`),
`analysis_type`. It is not translated to the MetaboLights vocabulary. The
Workbench SOURCE vocabulary has only `Blood` for biofluids, so serum vs plasma
must be checked per sample from the factor rows — and a bare `"plasma"` match
also catches `Seminal plasma`.

## Notes

- **Offline by default in tests**: pass an `httpx` transport to either client.
- **Strictly typed**: pydantic models; `pyright` strict; `py.typed` shipped.
- The DuckDB working-set layer (search → ingest → analyse) is the next branch;
  see `docs/api-design.md`.
