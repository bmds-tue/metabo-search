---
name: metabolights-search
description: Find and enrich MetaboLights metabolomics datasets. Search by experimental needs, score them, and generate per-sample biological sentences for embedding — disease state is the top signal. Use when a researcher needs datasets matching organism/tissue/technique/disease/sample-count/format.
---

# MetaboLights Dataset Search

Find datasets, pick the fit, then generate per-sample biological sentences.
**One import surface**: everything lives at `from mtbls_agent import ...`.

## Setup

```bash
cd /Users/frederikkaempchen/projects/metabolites-metadata-skill
source .venv/bin/activate      # uv-managed python 3.14
uv pip install -e .            # re-points the editable install to THIS dir
```

> The repo `.venv` is uv-managed. Rebuild if broken:
> `rm -rf .venv && uv venv && uv pip install -e .`
> Never use the homebrew system python (broken pyexpat).
> If `import mtbls_agent` resolves somewhere unexpected, run `uv pip install -e .` here.

---

## The One Flow (copy-paste)

```python
from mtbls_agent import (find_datasets, prepare_samples, load_samples,
    submit_samples, SampleSentencesStore, list_data_files,
    RequirementProfile, StudyRequirements, DownloadConfig)

# 1) Discovery — search + inspect + score + compare in one call.
profile = RequirementProfile(hard=StudyRequirements(organisms=["Homo sapiens"]))
report = find_datasets("urine alzheimer", profile=profile,
                       max_candidates=20, deep_inspect_top=8, max_workers=10)
for s in report.candidates:                    # ranked by score
    print(f"{s.study_id}  {s.score.overall:.2f}  {s.candidate.sample_count} samples")

# 2) Per-sample sentences — ONE LLM call per study, cached forever.
store = SampleSentencesStore("samples_cache.json")
for sc in report.candidates[:8]:              # deep-inspected ScoredCandidates
    d      = sc.candidate
    task   = prepare_samples(d, store)         # builds the single prompt
    descs  = load_samples(task)                # None unless cached
    if descs is None:
        descs = submit_samples(task, call_llm(task.profile_prompt))
    print(task.study_id, f"{len(descs)} sentences", "→", descs[0].sentence)
```

> `call_llm(prompt)` = **you** producing text with your own model. The library
> never calls an LLM; it hands you prompts and stores what you return.

---

## API table (call this, get that)

| Call | Returns | What it does |
|------|---------|--------------|
| `find_datasets(query, profile, ...)` | `ComparisonReport` | search → deep-inspect → score → rank (the whole discovery job) |
| `prepare_samples(deep_study, store)` | `SampleTask` | bundles contexts + the ONE profile prompt |
| `load_samples(task)` | `[SampleDescription] \| None` | cached sentences, else None |
| `submit_samples(task, llm_profile_text)` | `[SampleDescription]` | applies your LLM recipe to all samples, caches |
| `list_data_files(deep_study)` | `[DataFileRef]` | recursive `FILES/` listing w/ sizes |
| `download_data_files(deep_study, DownloadConfig)` | `DownloadResult` | selective download (bg: `start_download`) |
| `SampleManifest.build(deep, ...)` | `SampleManifest` | flat CSV + traceability (optional) |
| `search_studies / inspect_studies / score_studies / build_comparison_table` | — | the pieces, if you want control |
| `load_study_from_isa(id, dir)` | `StudyCandidate` | offline: rebuild from local ISA files |

You construct: `RequirementProfile`, `StudyRequirements`, `DownloadConfig`.

---

## What to decide (be fast, don't explore)

- **Discover** → `find_datasets` with the whole query + profile. Judge the top 2–3 by title/abstract/score.
- **RC/blank samples** → the library auto-tags them "quality control" — don't treat them as patients.
- **disease=unresolved** → check `desc.used_sources`. If a patient's disease didn't resolve (no linked files, unknown code), re-prompt the profile with the file-name codes or fix linkage. Don't accept silently.
- **Large studies** → always use a `SampleSentencesStore`; it's one LLM call and then forever free.
- **Reuse metadata** → `load_study_from_isa` if ISA files are already on disk; skip the network.
- **User error / API edge case** → note it and move on; don't loop.

## Errors & recovery
- `import mtbls_agent` resolves to a weird path → `uv pip install -e .` in this repo.
- Downloads timeout → library retries 3×; failing study stays shallow.
- `.venv` broken → `rm -rf .venv && uv venv && uv pip install -e .`.
