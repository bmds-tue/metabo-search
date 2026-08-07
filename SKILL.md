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