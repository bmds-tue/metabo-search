# metabo_search — API reference

Everything is importable from one surface: `from metabo_search import ...`

## Install / env

```bash
./scripts/install.sh          # sets up .venv-local, pip-installs the package,
                              # links the skill into pi/claude/opencode skill dirs
scripts/python -c "import metabo_search"   # venv python wrapper (portable)
```

Never use the system/homebrew python (broken pyexpat on some setups); always
`scripts/python` or `.venv-local/bin/python`.

## The two jobs

1. **Discovery** — find datasets matching requirements, rank them.
   Fully **deterministic** once a `RequirementProfile` exists (no LLM).
2. **Per-sample sentences** — human-readable, BioBERT-ready sentences.
   One LLM call per study (recipe), deterministic fill afterwards.

---

## Discovery functions

| Function | Returns | Notes |
|---|---|---|
| `find_datasets(query, profile, ...)` | `ComparisonReport` | API-filter by hard reqs → shallow screen → deep-inspect survivors → score → rank |
| `search_studies(query, ...)` | `[StudyCandidate]` | shallow, search-index only |
| `inspect_studies(cands, max_workers)` | `[StudyCandidate]` | deep: download + parse ISA |
| `load_study_from_isa(id, dir)` | `StudyCandidate` | offline rebuild from local ISA files |
| `score_studies(deep, profile)` | `[ScoredCandidate]` | hard pass/fail + soft 0-1, sorted |
| `screen_candidates(shallow, profile)` | `ScreeningResult` | deterministic shallow hard-pass + rank |
| `filter_by_maf(cands, ...)` | `[StudyCandidate]` | post-inspection: require or exclude MAF files (optionally min metabolites) |
| `analyze_maf_files(id, isa_dir\|maf_paths)` | `[MafAnalysis]` | **LLM-free MAF analysis**: metabolite count, sample count, names vs identifiers vs m/z-only |
| `render_maf_summary(analyses)` | `str` | one paste-ready text block from analyses |
| `profile_to_search_args(profile)` | `dict` | hard reqs → search API filters |
| `build_comparison_table(scored, profile)` | `ComparisonReport` | table rows/cols |

`report.screening` (when profile given) exposes `.survivors`, `.dropped`
[(candidate, reason)], `.shallow_scores` so you can explain *why* studies fail.

### RequirementProfile

```python
profile = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"], min_samples=50,
                           has_raw_data=True, techniques=["LC-MS"]),
    nice_to_have=StudyRequirements(techniques=["LC-MS"], data_formats=["mzML"]),
    free_text="untargeted lipidomics of human blood plasma",
)
```
Hard = non-negotiable (pass/fail). Nice-to-have = scored. `free_text` = soft
relevance hint. All fields optional. Shallow-checkable hard criteria: organisms,
sample_types, techniques, min_samples, has_raw_data, analysis_types.
`ionization_modes`, `data_formats`, **`has_maf`** and **`min_metabolites`** are
enforced **after** deep inspection (not in the shallow screen) — MAF presence
and metabolite counts only exist once `m_*.tsv` files are parsed.

**Vocabulary per repository (read before filling the fields):** each repo's
controlled values are matched literally (substring, case-insensitive), so the
wrong vocabulary silently under-recalls. MetaboLights `sample_types` are the
index's `organismParts.term` **facet values**: `"blood plasma"` (≈202 human
studies), `"blood serum"` (≈128) — NOT `"Serum"`/`"Plasma"` (~19/5), which
collapse the universe. Workbench `organisms` are the **Latin names**
(`"Homo sapiens"`, not `"Human"`); its SOURCE vocabulary has only `"Blood"`
for biofluids (serum/plasma must be verified per-sample from factors after
`inspect`); its disease values are title-cased canonicals (`"Cancer"`, `"Lung
cancer"`) and the matcher prefers an exact canonical over a fuzzy subtype.
Workbench `metabolite_count` is the identified-metabolite list length (no
sample columns); only MetaboLights MAF files give a true m×s matrix.

### MAF (metabolite assignment) files
`m_*.tsv` ISA-Tab files carry one row per identified metabolite (name,
formula, m/z, retention time, database, per-sample abundance columns).
Deep inspection surfaces them as `candidate.metabolite_count` and
`candidate.maf_files_parsed`; `filter_by_maf()` filters, `download_maf_files()`
fetches just the `m_*.tsv` files.

```python
from metabo_search import filter_by_maf, download_maf_files, \
    analyze_maf_files, render_maf_summary
kept = filter_by_maf(deep_candidates, require_maf=True, min_metabolites=100)
paths = download_maf_files("MTBLS1375", "./mafs")   # -> [./mafs/MTBLS1375/m_*.tsv]
analyses = analyze_maf_files("MTBLS1375", isa_dir="./mafs")
for a in analyses:
    print(a.summary)      # e.g. "286 metabolites, 88 samples, named (286/286)"
print(render_maf_summary(analyses))   # whole block, paste-ready
# a.metabolite_count, a.sample_count, a.sample_columns, a.has_names,
# a.has_identifiers, a.mz_only, a.annotation_level ('named'|'identified'|'mz_only')
```

Analysis is 100% local + deterministic — no LLM needed to get numbers; names vs
m/z-only classification is done by the library.

---

## Per-sample sentences (one LLM round-trip per study)

```python
from metabo_search import prepare_samples, submit_samples, load_samples, \
    revise_samples, SampleSentencesStore

store = SampleSentencesStore("samples_cache.json")
task  = prepare_samples(deep_study, store, revision=0)   # builds the ONE prompt
descs = load_samples(task)                                # None if not cached
if descs is None:
    descs = submit_samples(task, call_llm(task.profile_prompt))   # 1 LLM call
```

- `call_llm(prompt)` = **you** (the agent) generating text with your own model.
  The library never calls an LLM.
- `task.profile_prompt` instructs the LLM to thoroughly study the metadata
  (columns, sample rows, data-file name codes, factors, abstract) and return
  JSON: `codes` (disease/group decode), `sentence_template` (named `{slots}`),
  `slot_sources` (slot → where it comes from), `qc_string`, `study_context`.
- `submit_samples` applies deterministically to every sample and caches.
- **Feedback loop**: `revise_samples(task, new_text)` bumps the revision into
  its own cache slot; old wording is preserved for comparison.
- **QC enforced by the library**: QC/reference/dilution/blank/instrument-
  conditioning/`data-dependent-acquisition`/`Emergency`/`Solvent` samples always
  get `qc_string`, never a decoded disease.
- **`disease=unresolved`** appears in `desc.used_sources` when a non-QC sample's
  code couldn't be decoded (no linked files, unknown code) — act on it, don't
  accept silently.

### Codes live in factors too
`code` slots accept `field = "name" | "data_files" | <factor label>`.
Challenge codes like OGTT/OLTT/PAT/SLD often hide in factor values, not filenames.

---

## Download / formats

```python
from metabo_search import list_data_files, download_data_files, start_download, \
    DownloadConfig, format_summary

files = list_data_files(deep_study)               # recursive FILES/ walk + sizes
format_summary(deep_study)                        # {".mzml": n, ...} cheap probe
result = download_data_files(deep_study,
    DownloadConfig(categories=["derived"], file_types=[".mzml"], max_files=10))
result.dest_dir                                   # where files landed
task = start_download(deep_study, cfg); result = task.result()   # background
```

- `list_data_files` recurses into `FILES/RAW_FILES/`, `DERIVED_FILES/`, etc.
- Categorization is **directory-aware**: mzML under `RAW_FILES/` = raw.
- MS1 vs MS2 is **not** inferable from the index/filenames — needs a downloaded
  file or the paper.

---

## Manifest / export

```python
from metabo_search import SampleManifest
m = SampleManifest.build(deep, sentences_map={sid: descs},
                         data_files_map={sid: list_data_files(s)})
m.export_csv("samples.csv")
m.filter_samples(organism="Homo sapiens", tissue="brain")
```
Samples are keyed by **Sample Name** (e.g. `WCQA-073`) with Source Name
fallback — matching the sentence generator and the assay-derived file map.

---

## Tests

```bash
scripts/python -m pytest tests/ -q
```
Offline; real-world filenames from MTBLS719/1375/78/640/1333.

## Troubleshooting

- `import metabo_search` resolves elsewhere → run `scripts/install.sh`; it points
  `.venv-local` at this repo.
- Transient download failures → library retries with backoff+jitter; a failing
  study stays shallow — don't loop.
- Cache gives stale sentences after a recipe change → use `revise_samples`
  (new revision) instead of deleting the cache.