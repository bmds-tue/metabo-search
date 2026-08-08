---
name: metabolights-search
description: Find and enrich MetaboLights metabolomics datasets. Search by experimental needs, score them, and generate per-sample biological sentences for embedding — disease state is the top signal. Use when a researcher needs datasets matching organism/tissue/technique/disease/sample-count/format.
compatibility: Python 3.11+; uv optional (falls back to python3 -m venv)
metadata:
  install: scripts/install.sh
  package: mtbls_agent
---

# MetaboLights Dataset Search

Find datasets, pick the fit, then generate per-sample biological sentences.
**One import surface**: everything lives at `from mtbls_agent import ...`.

## Setup (run once, from the skill root)

```bash
./scripts/install.sh       # creates .venv-local, pip-installs the package,
                           # links the skill into pi/claude/opencode skill dirs
```

Use the venv python wrapper (portable, no hardcoded paths):

```bash
scripts/python -c "import mtbls_agent; print(mtbls_agent.__file__)"
```

> Never use the system/homebrew python (broken pyexpat on macOS). If
> `import mtbls_agent` resolves elsewhere, re-run `./scripts/install.sh`
> which re-points `.venv-local` at this repo. Uninstall: `scripts/uninstall.sh`.

Full API reference: see [references/api.md](references/api.md).

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
| `find_datasets(query, profile, ...)` | `ComparisonReport` | **deterministic** discovery: API-filter by hard reqs → shallow screen → deep-inspect survivors → score → rank. No LLM needed. |
| `screen_candidates(shallow, profile)` | `ScreeningResult` | deterministic hard-pass on search-index data + shallow rank (drop failures before the slow deep-inspect) |
| `profile_to_search_args(profile)` | `dict` | maps hard reqs to search API filters (server-side) |
| `prepare_samples(deep_study, store)` | `SampleTask` | bundles contexts + the ONE profile prompt |
| `load_samples(task)` | `[SampleDescription] \| None` | cached sentences, else None |
| `submit_samples(task, llm_profile_text)` | `[SampleDescription]` | applies your LLM recipe to all samples, caches |
| `list_data_files(deep_study)` | `[DataFileRef]` | recursive `FILES/` listing w/ sizes |
| `format_summary(deep_study)` | `dict[fmt→count]` | quick probe for mzML/RAW/.d presence (+ dir-aware raw/derived split) |
| `download_data_files(deep_study, DownloadConfig)` | `DownloadResult` | selective download (bg: `start_download`) |
| `SampleManifest.build(deep, ...)` | `SampleManifest` | flat CSV + traceability (optional) |
| `search_studies / inspect_studies / score_studies / build_comparison_table` | — | the pieces, if you want control |
| `load_study_from_isa(id, dir)` | `StudyCandidate` | offline: rebuild from local ISA files |

You construct: `RequirementProfile`, `StudyRequirements`, `DownloadConfig`.

---

## Deterministic vs AI — what needs the model

**Discovery is fully deterministic** once a `RequirementProfile` is structured:
server-side filter (hard reqs) → cheap shallow screen (drop hard-fails on the
search index, rank by soft score) → deep-inspect only survivors → score → rank.
No LLM in the loop (`report.screening` carries survivors + dropped-reasons so
you can explain *why* studies failed).

The model is only needed for two linguistic tasks:
1. Turning the user's **prose** into a `RequirementProfile` — skip if they fill
   the fields directly.
2. The per-study **sentence recipe** (1 cached call).

So if the researcher can state structured requirements, the whole collect→
analyze→summarize run is deterministic, reproducible, and fast.

## Sentence wording: what's in, what's out

The profile prompt enforces it, but OWN it when reviewing:
- **Include**: organism, tissue, disease/condition, biological state (fasted,
  baseline, post-challenge…), and biologically relevant factors (sex, age, group).
- **Exclude**: subject/participant IDs, barcodes, plate/well positions, and bare
  indices like "time point 1", "day 1", "replicate 3".
- **Rewrite** indices to meaning where the abstract supports it: "day 1 of
  fasting" → "after one day of fasting"; "time point 0" → "at baseline".
  Otherwise drop them.

## Wording feedback loop (until the user is happy)

Show the user 2–3 example sentences from the study. If the wording misses their
intent, revise and regenerate under a new revision — old wording is preserved
so they can compare:

```python
task   = prepare_samples(deep_study, store, revision=1)   # bump on each re-roll
descs  = load_samples(task)
if descs is None:
    descs = submit_samples(task, call_llm(task.profile_prompt))
```
Each `revision` gets its own cache slot, so feedback rounds never clobber each
other. Shortcut: `new_task = revise_samples(task, llm_text)` auto-bumps the
revision and caches; read results with `load_samples(new_task)`.

Codes that live in **factor values** (e.g. OGTT/OLTT/PAT/SLD) are decoded too:
the recipe's `code` slot accepts `field = <factor label>` (or `"data_files"`
to scan everything).

## What to decide (be fast, don't explore)

- **Discover** → `find_datasets` with the whole query + profile. Judge the top 2–3 by title/abstract/score.
- **RC/blank samples** → the library auto-tags them "quality control" — don't treat them as patients.
- **disease=unresolved** → check `desc.used_sources`. If a patient's disease didn't resolve (no linked files, unknown code), re-prompt the profile with the file-name codes or fix linkage. Don't accept silently.
- **Large studies** → always use a `SampleSentencesStore`; it's one LLM call and then forever free.
- **Reuse metadata** → `load_study_from_isa` if ISA files are already on disk; skip the network.
- **User error / API edge case** → note it and move on; don't loop.

## Errors & recovery
- `import mtbls_agent` resolves to a weird path → this repo shares a workspace
  with a parallel-test copy. Use the **private venv** so you always import THIS
  src: `uv venv .venv-local && uv pip install --python .venv-local/bin/python -e .`
  then run with `.venv-local/bin/python`. Never rely on the shared `.venv`'s
  editable pointer here.
- Downloads timeout → library retries with backoff+jitter; failing study stays
  shallow (don't loop).
- `.venv` broken → `rm -rf .venv && uv venv && uv pip install -e .`.

## Limits to be honest about
- The search index does NOT expose file formats or MS level. `format_summary()`
  can confirm mzML/RAW presence from filenames cheaply, but **MS1 vs MS2 can
  only be confirmed by opening a downloaded file or from the paper** — say so
  rather than guessing from the search result.
