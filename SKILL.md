---
name: metabolights-search
description: AI-native search engine for MetaboLights datasets. Search by natural language requirements, deep-inspect candidates in parallel, score against hard + nice-to-have criteria, and generate structured comparison reports. Use when a researcher needs to find metabolomics datasets matching specific experimental parameters.
---

# MetaboLights Dataset Search Skill

This skill provides a Python library (`mtbls_agent`) for finding, scoring, and comparing MetaboLights datasets based on experimental requirements.

## Setup

```bash
cd /Users/frederikkaempchen/projects/metabolites-metadata-skill
source .venv/bin/activate
```

The library is installed in development mode. If dependencies change:

```bash
source .venv/bin/activate
uv pip install -e .
```

## How to Use

### Step 1: Understand what the user needs

The user describes their ideal dataset in natural language. Extract:
- **Hard requirements** (non-negotiable): organism, sample type, technique, min samples, raw data, ionization mode, data format
- **Nice-to-haves** (scored): same categories but flexible
- **Free text**: the full description, used for relevance matching

Construct a `RequirementProfile`:

```python
from mtbls_agent import RequirementProfile, StudyRequirements

profile = RequirementProfile(
    hard=StudyRequirements(
        organisms=["Homo sapiens"],
        min_samples=50,
        has_raw_data=True,
    ),
    nice_to_have=StudyRequirements(
        techniques=["LC-MS"],
        data_formats=["mzML"],
    ),
    free_text="targeted lipidomics of human blood plasma in positive mode",
)
```

> **Prompt for the user:** If they're unsure what to specify, ask about:
> - Organism (human, mouse, rat, etc.)
> - Sample type (blood plasma, tissue, cells, urine, etc.)
> - Technique (LC-MS, GC-MS, NMR, etc.)
> - Analysis type (targeted, untargeted)
> - Minimum number of samples
> - Data format preference (mzML, RAW, .d, etc.)
> - Whether raw data files are required
> - Any specific keywords or topics

### Step 2: Search broadly

```python
from mtbls_agent import search_studies

results = search_studies(
    "lipidomics human blood plasma",
    page_size=100,    # get many candidates
    max_results=200,  # max to return
    organism="Homo sapiens",         # pre-filter (optional)
    technique="LC-MS",               # pre-filter (optional)
    min_samples=20,                  # pre-filter (optional)
)
```

The agent should **judge which look promising** based on titles/descriptions before deep inspection.

### Step 3: Deep-inspect promising candidates (parallel)

```python
from mtbls_agent import inspect_studies

# Pick the most promising 10-15 candidates
promising = results[:12]
deep = inspect_studies(
    promising,
    max_workers=10,   # parallel downloads + parsing
)
```

Each candidate now has: assays, protocols, sample metadata fields, metabolite counts, completeness score.

### Step 4: Score against requirements

```python
from mtbls_agent import score_studies

scored = score_studies(deep, profile)
# Returns list sorted by score, highest first
# .hard_passed = True/False
# .overall = 0-1 score
# .per_criterion = dict of individual scores
```

### Step 5: Build comparison table

```python
from mtbls_agent import build_comparison_table

report = build_comparison_table(scored, profile)
# report.table_rows: list of [study_id, title, organisms, techniques, samples, ...]
# report.table_columns: column headers
```

### Step 6: Write a narrative summary

Using the structured `ComparisonReport`, write a natural language summary:

```
Based on your requirements, here are the top matches:

1. **MTBLS1375** (score 0.89) ✓ Hard requirements met
   - 145 human blood plasma samples, targeted LC-MS lipidomics
   - Agilent 6495A Triple Quadrupole, positive ionization
   - DOI: 10.1038/s41467-020-15960-z
   - 92% metadata completeness — excellent!

2. **MTBLS10722** (score 0.75) ✓ Hard requirements met  
   - 132 human samples, LC-MS lipidomics of whole blood
   - Only shallow metadata available (FTP timeout)
   - Consider if whole blood works or you need plasma specifically
```

### Step 6b: Generate per-sample biological sentences (for BioBERT embedding)

**ONE LLM call per study, then zero per-sample calls.** The LLM first
THOROUGHLY studies the study's metadata structure, decodes every code
(especially disease/group), and authors a **sentence recipe**. The library
then applies it deterministically to every sample.

**STEP 1 — The Agent must study the metadata structure before writing.**

Build the study-profile prompt, which lays out the ISA columns, example
sample rows, data-file name stems (where disease codes hide), factor values,
and the abstract:

```python
from mtbls_agent.sample_gen import (
    build_study_profile_prompt, collect_sample_contexts,
    parse_study_profile, apply_recipe,
    SampleDescription, SampleSentencesStore,
)

profile_prompt = build_study_profile_prompt(deep_study)
# The AGENT calls its LLM with this prompt.
# The LLM output is ONE JSON object:
#   {
#     "codes": {"ALZ": "Alzheimer's disease", "CTL": "cognitively normal control", ...},
#     "sentence_template": "Homo sapiens {tissue} from an {disease} patient, {Sex}, age {Age}, ...",
#     "slot_sources": {"disease": {"type": "code", "field": "data_files"}, ...},
#     "qc_string": "quality control sample",
#     "study_context": "..."
#   }
profile = parse_study_profile(llm_json)
```

**The prompt's instructions are explicit — the LLM must:**
1. Study the ISA sample-file **columns** and example **rows** (see where each
   value lives)
2. Study the **data-file name stems** (decode disease/group codes like ALZ)
3. Study the **factor** names and unique values
4. Read the **abstract**
5. Author a `sentence_template` with named `{slots}` + a `slot_sources` map
   saying, for every slot, which source it comes from (factor / characteristic
   / decoded code / tissue / sample_type).

**STEP 2 — Apply the recipe deterministically (no per-sample LLM):**

```python
contexts = collect_sample_contexts(deep_study)     # full per-sample context
descriptions = [
    apply_recipe(ctx, profile) for ctx in contexts  # 0 LLM calls here
]
# e.g. "Homo sapiens urine from an Alzheimer's disease patient, Female, age 69,
#       (Sample Dilution: 100)."
```

QC/reference/dilution/blank samples get the `qc_string` (e.g. "quality
control sample") instead of an invented disease.

**STEP 3 — Cache results so re-runs are free:**

```python
store = SampleSentencesStore("samples_cache.json")
key = store.key_for(deep_study)          # id + data hash
if key in store:
    descriptions = store.load(key)        # skip the LLM entirely
else:
    # ... run STEP 1 (1 LLM call) + STEP 2 (deterministic) ...
    store.save(key, descriptions)
```

**Why this works and scales:** 1–2 LLM calls per study regardless of sample
count (a 1753-sample cohort = ~1 call + deterministic fill). Disease state is
captured because the LLM studies the file-name codes during profile creation
and the recipe's `{disease}` slot decodes each sample's own code. This is the
recommended path for BioBERT-ready per-sample sentences.

### Step 6c: Download data files (selective, by format)

### Step 6d: Build a SampleManifest (traceability map)

The manifest links every sample to its ISA metadata, generated sentence, and
data files — in both hierarchical (study → samples) and flat (CSV) forms.

```python
from mtbls_agent.manifest import SampleManifest

manifest = SampleManifest.build(
    candidates=deep_candidates,
    sentences_map={s.study_id: build_sample_sentences(s) for s in deep_candidates},
    data_files_map={s.study_id: list_data_files(s) for s in deep_candidates},
)

# Explore hierarchically
study = manifest.studies["MTBLS1375"]
print(f"{study.study_id}: {study.sample_count} samples")
for sample in study.samples[:3]:
    print(f"  {sample.sample_name}: {sample.organism} {sample.tissue}")
    print(f"    factors: {sample.factors}")
    print(f"    sentence: {sample.sentence[:80]}...")

# Export flat CSV for ML
manifest.export_csv("samples.csv")

# Filter by biology
human_brain = manifest.filter_samples(organism="Homo sapiens", tissue="brain")
print(f"{len(human_brain)} human brain samples")

# Get all sentences for BioBERT
sentences = manifest.all_sentences
```

CSV columns: ``sample``, ``study``, ``organism``, ``tissue``, ``variant``,
``sample_type``, ``factor_*`` (dynamic), ``assay_techniques``,
``assay_instruments``, ``sentence``, ``raw_data_files``,
``derived_data_files``, ``has_raw_data``.

### Step 7: Iterate

For background (non-blocking) downloads, use ``start_download``:

```python
from mtbls_agent.downloader import start_download

task = start_download(deep_study, DownloadConfig(categories=["raw"], max_files=5))
# Returns immediately — download runs in background thread
# ... do other work (score, summarize, write narrative) ...
result = task.result()  # blocks only when you need the result
print(f"Downloaded {len(result.downloaded)} files, {result.total_bytes / 1e6:.0f} MB")
```

For synchronous download (blocks until finished):

```python
from mtbls_agent.downloader import list_data_files, download_data_files, DownloadConfig

```python
from mtbls_agent.downloader import list_data_files, download_data_files, DownloadConfig

# List what's available before downloading
files = list_data_files(deep_study)
raw = [f for f in files if f.category == 'raw']
derived = [f for f in files if f.category == 'derived']
print(f"{len(raw)} raw files, {len(derived)} derived files")

# Download only mzML files (derived/processed)
config = DownloadConfig(
    file_types=[".mzml", ".mzxml"],    # filter by extension
    categories=["derived"],             # or "raw", "maf"
    max_files=10,                        # limit count
    parallel_downloads=4,               # concurrent downloads
)
result = download_data_files(deep_study, config)
print(f"Downloaded {len(result.downloaded)} files ({result.total_bytes / 1e6:.0f} MB)")

# Or download only raw instrument data
config2 = DownloadConfig(
    categories=["raw"],
    file_types=[".d.zip", ".raw", ".d"],
    max_files=5,
)
result2 = download_data_files(deep_study, config2)
```

Filters available: ``file_types``, ``categories`` (raw/derived/maf/other),
``sample_names``, ``max_files``, ``max_size_gb``.

Notes:
- ``list_data_files`` **recurses** into ``FILES/`` subdirectories (some studies
  use ``FILES/RAW_FILES/``, ``FILES/DERIVED_FILES/``, …)
- Downloads retry automatically on transient SSL/timeout errors
- The manifest links samples to data files via the **assay files**'
  ``Raw/Derived Spectral Data File`` columns (exact), falling back to
  token matching when a sample isn't in an assay file

### Step 7: Iterate


If the user isn't satisfied, refine the search:
- Adjust hard requirements based on what's available
- Try different query terms
- Focus on specific techniques or organisms
- Increase the number of candidates to inspect

## End-to-End Shortcut

For simple cases, use the pipeline wrapper:

```python
from mtbls_agent import find_datasets

report = find_datasets(
    query="lipidomics human blood plasma",
    profile=profile,
    max_candidates=100,
    deep_inspect_top=10,
    max_workers=10,
)
```

## Tips for the Agent

1. **Guide the user** to describe their needs using the prompt template above
2. **Judge candidate quality** from titles and descriptions before deep inspection
3. **Explain the scores** — a high score means a strong match, but a low score doesn't mean bad data, just a mismatch with requirements
4. **Be transparent** about hard failures — tell the user WHY a study didn't pass
5. **Suggest refinements** — if nothing passes hard requirements, suggest relaxing them
6. **The comparison table** shows all candidates at a glance; use it to contrast options