# metabo-search — API reference (import: metabo_search) (auto-generated)

> Regenerate anytime: `scripts/python scripts/gen_api_docs.py`

## Functions

### `analyze_maf_files(study_id: 'str', isa_dir: 'str | Path | None' = None, maf_paths: 'list[str | Path] | None' = None, max_examples: 'int' = 5) -> 'list[MafAnalysis]'`

Analyze the MAF (`m_*.tsv`) files of a study — locally, no LLM. Provide either downloaded MAF paths (`maf_paths`, e.g. from :func:`download_maf_files`) or an ISA directory (`isa_dir`) holding the files. `{isa_dir}/{study_id}/` layout (the downloader's layout) is also searched, so `analyze_maf_files(id, download_root)` works directly. Parameters ---------- study_id : str MetaboLights accession…

### `apply_recipe(context: 'SampleContext', profile: 'StudyProfile') -> 'SampleDescription'`

Deterministically fill the recipe template for one sample.

### `build_comparison_table(scored: 'list[ScoredCandidate]', profile: 'RequirementProfile | None' = None) -> 'ComparisonReport'`

Build a structured comparison table from scored candidates. The table has one row per candidate and columns for key criteria (study ID, title, organism, technique, sample count, raw files, completeness, overall score, hard-passed status).

### `build_study_profile_prompt(candidate: 'StudyCandidate') -> 'str'`

Build the ONE prompt the agent's LLM uses to author the study recipe.

### `collect_sample_contexts(candidate: 'StudyCandidate') -> 'list[SampleContext]'`

Collect the full raw context for every sample in a study. Includes factors, characteristics, and crucially the **associated data file names** (from `sample_file_map`), which often carry the disease code (e.g. `ALZ_RNEG_ToF03_U4W15.mzML`).

### `custom(name: 'str', **params: 'Any') -> 'Custom'`

Custom predicate reference (must be registered before validate/run).

### `describe(top: 'int' = 3, revision: 'int' = 0, store: 'str | None' = None, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `download(dest_dir: 'str' = '.', categories=None, file_types=None, sample_names=None, max_files: 'int | None' = None, max_size_gb: 'float | None' = None, parallel: 'int' = 4, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `download_data_files(candidate: 'StudyCandidate', config: 'DownloadConfig | None' = None) -> 'DownloadResult'`

Download data files matching the given config filters.

### `download_maf_files(study_id: 'str', dest: 'str') -> 'list[Path]'`

Download ONLY the MAF (metabolite assignment) files for a study. MAF files are the `m_*.tsv` ISA-Tab files: one row per identified metabolite, with per-sample abundance columns. They are the metadata source for `metabolite_count` and `maf_files_parsed`. Parameters ---------- study_id : str MetaboLights accession (e.g. `"MTBLS1375"`). dest : str Directory to save into; files land in…

### `export(path: 'str' = 'manifest.csv', include_sentences: 'bool' = True, with_traceability: 'bool' = True, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `filter(*predicates: 'Any', name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `filter_by_maf(candidates: 'list[StudyCandidate]', *, require_maf: 'bool' = True, min_metabolites: 'int | None' = None) -> 'list[StudyCandidate]'`

Post-inspection filter on MAF (metabolite assignment file) presence. Runs on DEEP-inspected candidates (the search index does not expose MAF files — they are only known after :func:`inspect_studies`). Keeps candidates in input order. Parameters ---------- candidates : list[StudyCandidate] Deep-inspected candidates (`maf_files_parsed` populated). require_maf : bool True: keep only studies that…

### `find_datasets(query: 'str' = '', *, profile=None, databases=None, max_candidates: 'int' = 100, deep_inspect_top: 'int' = 10, max_workers: 'int' = 10, min_survivors: 'int | None' = None, organism=None, technique=None, sample_type=None, min_samples: 'int | None' = None) -> 'ComparisonReport'`

End-to-end discovery, deterministic when `profile` is structured. Pipeline (no LLM involved): 1. Push hard requirements into the search API (server-side filter). 2. Screen the shallow results: drop hard-fails, rank survivors. 3. Deep-inspect only the top survivors (bounded by the screen cap). 4. Full deterministic scoring + comparison table. The legacy entry point — equivalent to…

### `format_summary(candidate: 'StudyCandidate') -> 'dict[str, int]'`

Count data files by format, from a recursive FILES/ listing. A quick probe for "does this study have mzML / RAW / .d?" without downloading anything. Categories reflect the directory (RAW_FILES vs DERIVED_FILES) when available.

### `full_report(query: 'str', profile=None, *, databases=None, top: 'int' = 3, revision: 'int' = 0, store: 'str | None' = None, page_size: 'int' = 100, max_results: 'int' = 200, min_survivors: 'int' = 10, workers: 'int' = 10, cache_root=None) -> 'Pipeline'`

Discovery + per-sample sentences (ONE LLM call per study, cached).

### `harvest(query: 'str', profile=None, *, databases=None, download_kwargs: 'dict[str, Any] | None' = None, export_kwargs: 'dict[str, Any] | None' = None, top: 'int' = 3, page_size: 'int' = 100, max_results: 'int' = 200, min_survivors: 'int' = 10, workers: 'int' = 10, cache_root=None) -> 'Pipeline'`

Full report + selective download + manifest export. `download_kwargs` must constrain what is downloaded (categories / file_types / sample_names / max_files / max_size_gb) — harvest never silently downloads everything.

### `inspect(workers: 'int' = 10, tmp_dir: 'str | None' = None, parse_workers: 'int | None' = None, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `inspect_studies(candidates: 'list[StudyCandidate]', max_workers: 'int' = 10, tmp_dir: 'str | None' = None, download_data_files: 'bool' = False, parse_workers: 'int | None' = None) -> 'list[StudyCandidate]'`

Deep-inspect candidates in parallel. Two phases: 1. **Download** (threads, I/O-bound): fetch the ISA-Tab files to disk. 2. **Parse** (threads, or *processes* when it pays): CPU-bound ISA parsing runs on real cores — threads are GIL-serialized (~2x+ faster for full-scan batches). Parameters ---------- candidates : list[StudyCandidate] Shallow candidates from phase 1. max_workers : int Parallel…

### `list_data_files(candidate: 'StudyCandidate', cache_dir: 'str | Path | None' = None) -> 'list[DataFileRef]'`

List data files recursively through FILES/ and its subdirectories. Some studies organize data in FILES/RAW_FILES/, FILES/DERIVED_FILES/, etc. Walks the HTTP directory tree, parsing filenames + sizes from HTML tables. Parameters ---------- candidate : StudyCandidate Deep (or shallow) candidate to list. cache_dir : str | Path | None Optional directory for a per-study listing cache (`…

### `load_samples(task: 'SampleTask') -> 'list[SampleDescription] | None'`

Return cached descriptions for this task, or None if not cached.

### `load_study_from_isa(study_id: 'str', isa_dir: 'str | Path') -> 'StudyCandidate'`

Reconstruct a deep-inspected StudyCandidate from LOCAL ISA files. Offline / reuse path: if ISA-Tab files (i_*.txt, s_*.txt, a_*.txt, m_*.tsv) are already on disk, parse them without any network access — the same parsers used by :func:`inspect_studies`. Parameters ---------- study_id : str MetaboLights accession (e.g. `"MTBLS1375"`). isa_dir : str | Path Directory containing the ISA metadata…

### `maf(require: 'bool' = True, min_metabolites: 'int | None' = None) -> 'Maf'`

MAF (metabolite assignment) predicate (applies to InspectResult).

### `parse_study_profile(llm_json: 'str') -> 'StudyProfile'`

Parse the LLM's JSON response into a :class:`StudyProfile`. Tolerates fenced/marked code blocks.

### `pipeline(*steps: 'Step', input: 'Result | None' = None, cache_root=None) -> 'Pipeline'`



### `prepare_samples(candidate: 'StudyCandidate', store: 'SampleSentencesStore | None' = None, revision: 'int' = 0) -> 'SampleTask'`

Bundle everything for one study: contexts + the single LLM prompt. `revision` feeds the cache key: author a revised profile under a new revision so the feedback loop can compare wordings without clobbering.

### `profile_to_search_args(profile) -> 'dict'`

Deterministically map a profile's HARD requirements to search kwargs, so the search API pre-filters (server-side) instead of fetching everything. Only criteria the API/filter can express are mapped: organism, technique, sample_type, min_samples. Everything else is enforced later (shallow or deep screening).

### `quick_discovery(query: 'str', profile=None, *, databases=None, page_size: 'int' = 100, max_results: 'int' = 200, min_survivors: 'int' = 10, workers: 'int' = 10, cache_root=None) -> 'Pipeline'`

Stage 2 (the old find_datasets): probe + deep inspect + scored ranking.

### `quick_probe(query: 'str', profile=None, *, databases=None, page_size: 'int' = 100, max_results: 'int' = 200, min_survivors: 'int' = 10) -> 'Pipeline'`

Stage 1: narrow the profile cheaply. Search replays warm across edits.

### `register_predicate(name: 'str', applies_to: 'type', fn: 'Callable') -> 'None'`

Register a custom filter predicate by name (configs stay plain data).

### `registered_predicates() -> 'list[str]'`



### `render_maf_summary(analyses: 'list[MafAnalysis]') -> 'str'`

Collapse MAF analyses into one paste-ready text block. `""` when there are no analyses; otherwise one `summary` line per file.

### `revise_samples(task: 'SampleTask', profile_json: 'str') -> 'SampleTask'`

Author a revised wording under the next revision and cache it. Returns the *new* task (already submitted). Read results with `load_samples(new_task)`. Old wording remains under the previous revision, so the user can compare without clobbering.

### `score(profile: 'RequirementProfile | None' = None, columns: 'list[str] | None' = None, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `score_studies(candidates: 'list[StudyCandidate]', profile: 'RequirementProfile') -> 'list[ScoredCandidate]'`

Score each candidate against the requirement profile. Returns scored candidates sorted descending by overall score. Candidates that fail a hard requirement are at the bottom (score=0).

### `screen(profile: 'RequirementProfile | None' = None, min_survivors: 'int | None' = None) -> 'Screen'`

Shallow screening predicate (applies to SearchResult). `min_survivors=None` ⇒ never truncate (identity); set a cap to bound the next stage (recipes set 10).

### `screen_candidates(candidates: 'list[StudyCandidate]', profile: 'RequirementProfile', min_survivors: 'int' = 10) -> 'ScreeningResult'`

Deterministic pre-screen on search-index data (no LLM, no download). Drops candidates that fail shallow-checkable hard constraints, ranks the rest by a shallow soft score, and keeps at least `min_survivors` (or the total that pass) for deep inspection. Deterministic and cheap: only fields already present in the search hit.

### `search(query: 'str', *, profile=None, databases=None, page_size: 'int' = 100, max_results: 'int' = 200, filters: 'list[dict] | None' = None, ms_filters: 'dict | None' = None, sort_field: 'str | None' = None, sort_direction: 'str' = 'desc', organism=None, technique=None, sample_type=None, min_samples: 'int | None' = None, min_raw_files: 'int | None' = None, name: 'str | None' = None, cache: 'CacheOpts | None' = None, print_opts: 'PrintOpts | None' = None) -> 'Step'`



### `search_studies(query: 'str' = '', *, page_size: 'int' = 100, filters: 'list[dict[str, Any]] | None' = None, sort_field: 'str | None' = None, sort_direction: 'str' = 'desc', ms_filters: 'dict[str, Any] | None' = None, organism: 'str | list[str] | None' = None, technique: 'str | list[str] | None' = None, sample_type: 'str | list[str] | None' = None, min_samples: 'int | None' = None, min_raw_files: 'int | None' = None, max_results: 'int' = 200) -> 'list[StudyCandidate]'`

Broad search returning shallow `StudyCandidate` objects. This is **Phase 1** — get many candidates quickly from the search index. Results contain search-level metadata (organisms, techniques, sample count, file counts, description, publications …) but **not** full ISA-Tab detail. Parameters ---------- query : str Free-text search (e.g. `"lipidomics human blood plasma"`). page_size : int Results…

### `start_download(candidate, config=None)`



### `submit_samples(task: 'SampleTask', profile_json: 'str') -> 'list[SampleDescription]'`

Apply the LLM-authored profile to every sample; cache and return. `profile_json` is the raw text the agent's LLM produced in answer to `task.profile_prompt`.

## Classes / constructors

### `CacheOpts(enabled: 'bool | None' = None, ttl: 'str | None' = None) -> None`

CacheOpts(enabled: 'bool | None' = None, ttl: 'str | None' = None)

- `enabled`: `bool | None`
- `ttl`: `str | None`

### `Custom(name: 'str', params: 'dict' = <factory>) -> None`

Custom(name: 'str', params: 'dict' = )

- `name`: `str`
- `params`: `dict`

### `DescribeResult(by_study: 'dict[str, list[SampleDescription]]' = <factory>, revision: 'int' = 0, reused: 'dict[str, bool]' = <factory>) -> None`

DescribeResult(by_study: 'dict[str, list[SampleDescription]]' = , revision: 'int' = 0, reused: 'dict[str, bool]' = )

- `by_study`: `dict[str, list[SampleDescription]]`
- `revision`: `int`
- `reused`: `dict[str, bool]`

### `DownloadConfig(file_types: 'list[str] | None' = None, sample_names: 'list[str] | None' = None, categories: 'list[str] | None' = None, dest_dir: 'str | None' = None, max_files: 'int | None' = None, max_size_gb: 'float | None' = None, parallel_downloads: 'int' = 4, files_cache_dir: 'str | None' = None) -> None`

DownloadConfig(file_types: 'list[str] | None' = None, sample_names: 'list[str] | None' = None, categories: 'list[str] | None' = None, dest_dir: 'str | None' = None, max_files: 'int | None' = None, max_size_gb: 'float | None' = None, parallel_downloads: 'int' = 4, files_cache_dir: 'str | None' = None)

- `file_types`: `list[str] | None`
- `sample_names`: `list[str] | None`
- `categories`: `list[str] | None`
- `dest_dir`: `str | None`
- `max_files`: `int | None`
- `max_size_gb`: `float | None`
- `parallel_downloads`: `int`
- `files_cache_dir`: `str | None`

### `DownloadResult(dest_dir: 'str' = '', downloaded: 'list[str]' = <factory>, total_bytes: 'int' = 0, failed: 'list[str]' = <factory>) -> None`

DownloadResult(dest_dir: 'str' = '', downloaded: 'list[str]' = , total_bytes: 'int' = 0, failed: 'list[str]' = )

- `dest_dir`: `str`
- `downloaded`: `list[str]`
- `total_bytes`: `int`
- `failed`: `list[str]`

### `ExportResult(path: 'str' = '', rows: 'int' = 0, columns: 'list[str]' = <factory>) -> None`

ExportResult(path: 'str' = '', rows: 'int' = 0, columns: 'list[str]' = )

- `path`: `str`
- `rows`: `int`
- `columns`: `list[str]`

### `FilterResult(survivors: 'list[StudyCandidate]', dropped: 'list[tuple[StudyCandidate, str]]' = <factory>, order: 'dict[str, float]' = <factory>, stage: 'str' = '') -> None`

FilterResult(survivors: 'list[StudyCandidate]', dropped: 'list[tuple[StudyCandidate, str]]' = , order: 'dict[str, float]' = , stage: 'str' = '')

- `survivors`: `list[StudyCandidate]`
- `dropped`: `list[tuple[StudyCandidate, str]]`
- `order`: `dict[str, float]`
- `stage`: `str`

### `InspectResult(candidates: 'list[StudyCandidate]', isa_dirs: 'dict[str, str]' = <factory>) -> None`

InspectResult(candidates: 'list[StudyCandidate]', isa_dirs: 'dict[str, str]' = )

- `candidates`: `list[StudyCandidate]`
- `isa_dirs`: `dict[str, str]`

### `Maf(require: 'bool' = True, min_metabolites: 'int | None' = None, name: 'str' = 'maf') -> None`

Maf(require: 'bool' = True, min_metabolites: 'int | None' = None, name: 'str' = 'maf')

- `require`: `bool`
- `min_metabolites`: `int | None`
- `name`: `str`

### `MafAnalysis(study_id: 'str' = '', file_name: 'str' = '', file_path: 'str' = '', metabolite_count: 'int' = 0, sample_count: 'int' = 0, sample_columns: 'list[str]' = <factory>, named_count: 'int' = 0, identified_count: 'int' = 0, mz_count: 'int' = 0, annotation_level: 'str' = 'empty', examples: 'list[str]' = <factory>, parse_error: 'str' = '') -> None`

What one MAF file contains, in answer-ready numbers. The :attr:`summary` property renders everything into one line the agent can paste straight into its answer / reasoning.

- `study_id`: `str`
- `file_name`: `str`
- `file_path`: `str`
- `metabolite_count`: `int`
- `sample_count`: `int`
- `sample_columns`: `list[str]`
- `named_count`: `int`
- `identified_count`: `int`
- `mz_count`: `int`
- `annotation_level`: `str`
- `examples`: `list[str]`
- `parse_error`: `str`

### `Pipeline(*steps: 'Step', input: 'Result | None' = None, cache_root: 'str | Path | None' = None)`

An ordered list of typed steps. Immutable: builders return a new one. - `run(llm=...)` folds the steps; the cache root replays warm steps (each step's cache key covers kind + config + input digest). - `input=` at construction starts midstream from any typed result. - `extend` / slicing give "stop anywhere, continue via warm cache".

### `PipelineResult(items: 'list[tuple[str, Result]]')`

Ordered, typed mapping of step keys → results (r["score"], r.score).

### `PrintOpts(top: 'int' = 10, detail: 'bool' = False) -> None`

PrintOpts(top: 'int' = 10, detail: 'bool' = False)

- `top`: `int`
- `detail`: `bool`

### `RequirementProfile(hard: 'StudyRequirements' = <factory>, nice_to_have: 'StudyRequirements' = <factory>, free_text: 'str' = '') -> None`

What a researcher is looking for. - `hard`: non-negotiable filters (pass/fail) - `nice_to_have`: scored criteria (higher = better) - `free_text`: natural language description used for relevance boosting

- `hard`: `StudyRequirements`
- `nice_to_have`: `StudyRequirements`
- `free_text`: `str`

### `SampleManifest(studies: 'dict[str, StudyEntry]' = <factory>, samples: 'dict[str, list[SampleEntry]]' = <factory>, table_columns: 'list[str]' = <factory>, table_rows: 'list[list[Any]]' = <factory>) -> None`

SampleManifest(studies: 'dict[str, StudyEntry]' = , samples: 'dict[str, list[SampleEntry]]' = , table_columns: 'list[str]' = , table_rows: 'list[list[Any]]' = )

- `studies`: `dict[str, StudyEntry]`
- `samples`: `dict[str, list[SampleEntry]]`
- `table_columns`: `list[str]`
- `table_rows`: `list[list[Any]]`

### `SampleSentencesStore(path: 'str | Path' = '.agents/sample_cache.json')`

File-backed cache of generated sample descriptions, keyed by study.

### `SampleTask(study_id: 'str', cache_key: 'str', contexts: 'list[SampleContext]', profile_prompt: 'str', store: "'SampleSentencesStore | None'" = None, revision: 'int' = 0, data_hash: 'str' = '') -> None`

Everything the agent needs for ONE study-level LLM round trip. Flow:: task = prepare_samples(deep_study, store) descs = load_samples(task) # None if not cached if descs is None: descs = submit_samples(task, call_llm(task.profile_prompt))

- `study_id`: `str`
- `cache_key`: `str`
- `contexts`: `list[SampleContext]`
- `profile_prompt`: `str`
- `store`: `'SampleSentencesStore | None'`
- `revision`: `int`
- `data_hash`: `str`

### `ScoreResult(ranked: 'list[ScoredCandidate]', table: 'ComparisonReport') -> None`

ScoreResult(ranked: 'list[ScoredCandidate]', table: 'ComparisonReport')

- `ranked`: `list[ScoredCandidate]`
- `table`: `ComparisonReport`

### `Screen(profile: 'RequirementProfile | None' = None, min_survivors: 'int | None' = None, name: 'str' = 'screen') -> None`

Screen(profile: 'RequirementProfile | None' = None, min_survivors: 'int | None' = None, name: 'str' = 'screen')

- `profile`: `RequirementProfile | None`
- `min_survivors`: `int | None`
- `name`: `str`

### `SearchResult(candidates: 'list[StudyCandidate]', query: 'str' = '', args_used: 'dict[str, Any]' = <factory>) -> None`

SearchResult(candidates: 'list[StudyCandidate]', query: 'str' = '', args_used: 'dict[str, Any]' = )

- `candidates`: `list[StudyCandidate]`
- `query`: `str`
- `args_used`: `dict[str, Any]`

### `StudyCandidate(study_id: 'str', title: 'str' = '', description: 'str' = '', status: 'str' = '', repository: 'str' = 'metabolights', organisms: 'list[OntologyTerm]' = <factory>, organism_parts: 'list[OntologyTerm]' = <factory>, assay_techniques: 'list[dict]' = <factory>, design_descriptors: 'list[OntologyTerm]' = <factory>, technology_types: 'list[OntologyTerm]' = <factory>, factors: 'list[OntologyTerm]' = <factory>, sample_count: 'int | None' = None, raw_file_count: 'int | None' = None, derived_file_count: 'int | None' = None, assay_count: 'int | None' = None, size_in_bytes: 'int | None' = None, publications: 'list[PublicationInfo]' = <factory>, contacts: 'list[str]' = <factory>, submitters: 'list[str]' = <factory>, submission_date: 'str' = '', public_release_date: 'str' = '', assays: 'list[AssayInfo]' = <factory>, data_files: 'list[DataFileInfo]' = <factory>, protocols: 'list[ProtocolInfo]' = <factory>, sample_metadata_fields: 'list[str]' = <factory>, sample_metadata: 'list[dict[str, str]]' = <factory>, metabolite_count: 'int | None' = None, metadata_completeness: 'float' = 0.0, investigation_file_parsed: 'bool' = False, assay_files_parsed: 'bool' = False, sample_file_parsed: 'bool' = False, maf_files_parsed: 'bool' = False, sample_file_map: 'dict[str, dict[str, list[str]]]' = <factory>, _raw_api_result: 'dict[str, Any]' = <factory>) -> None`

All known information about a MetaboLights study. Phase 1 (shallow) fields come from the search API. Phase 2 (deep) fields are populated after downloading + parsing ISA files.

- `study_id`: `str`
- `title`: `str`
- `description`: `str`
- `status`: `str`
- `repository`: `str`
- `organisms`: `list[OntologyTerm]`
- `organism_parts`: `list[OntologyTerm]`
- `assay_techniques`: `list[dict]`
- `design_descriptors`: `list[OntologyTerm]`
- `technology_types`: `list[OntologyTerm]`
- `factors`: `list[OntologyTerm]`
- `sample_count`: `int | None`
- `raw_file_count`: `int | None`
- `derived_file_count`: `int | None`
- `assay_count`: `int | None`
- `size_in_bytes`: `int | None`
- `publications`: `list[PublicationInfo]`
- `contacts`: `list[str]`
- `submitters`: `list[str]`
- `submission_date`: `str`
- `public_release_date`: `str`
- `assays`: `list[AssayInfo]`
- `data_files`: `list[DataFileInfo]`
- `protocols`: `list[ProtocolInfo]`
- `sample_metadata_fields`: `list[str]`
- `sample_metadata`: `list[dict[str, str]]`
- `metabolite_count`: `int | None`
- `metadata_completeness`: `float`
- `investigation_file_parsed`: `bool`
- `assay_files_parsed`: `bool`
- `sample_file_parsed`: `bool`
- `maf_files_parsed`: `bool`
- `sample_file_map`: `dict[str, dict[str, list[str]]]`
- `_raw_api_result`: `dict[str, Any]`

### `StudyRequirements(organisms: 'list[str] | None' = None, sample_types: 'list[str] | None' = None, diseases: 'list[str] | None' = None, techniques: 'list[str] | None' = None, ionization_modes: 'list[str] | None' = None, analysis_types: 'list[str] | None' = None, instrument_models: 'list[str] | None' = None, data_formats: 'list[str] | None' = None, min_samples: 'int | None' = None, has_raw_data: 'bool | None' = None, has_derived_data: 'bool | None' = None, has_maf: 'bool | None' = None, min_metabolites: 'int | None' = None) -> None`

A set of requirements (used for both hard and nice-to-have).

- `organisms`: `list[str] | None`
- `sample_types`: `list[str] | None`
- `diseases`: `list[str] | None`
- `techniques`: `list[str] | None`
- `ionization_modes`: `list[str] | None`
- `analysis_types`: `list[str] | None`
- `instrument_models`: `list[str] | None`
- `data_formats`: `list[str] | None`
- `min_samples`: `int | None`
- `has_raw_data`: `bool | None`
- `has_derived_data`: `bool | None`
- `has_maf`: `bool | None`
- `min_metabolites`: `int | None`
