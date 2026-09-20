# mtbls_agent — API reference (auto-generated)

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

### `download_data_files(candidate: 'StudyCandidate', config: 'DownloadConfig | None' = None) -> 'DownloadResult'`

Download data files matching the given config filters.

### `download_maf_files(study_id: 'str', dest: 'str') -> 'list[Path]'`

Download ONLY the MAF (metabolite assignment) files for a study. MAF files are the `m_*.tsv` ISA-Tab files: one row per identified metabolite, with per-sample abundance columns. They are the metadata source for `metabolite_count` and `maf_files_parsed`. Parameters ---------- study_id : str MetaboLights accession (e.g. `"MTBLS1375"`). dest : str Directory to save into; files land in…

### `filter_by_maf(candidates: 'list[StudyCandidate]', *, require_maf: 'bool' = True, min_metabolites: 'int | None' = None) -> 'list[StudyCandidate]'`

Post-inspection filter on MAF (metabolite assignment file) presence. Runs on DEEP-inspected candidates (the search index does not expose MAF files — they are only known after :func:`inspect_studies`). Keeps candidates in input order. Parameters ---------- candidates : list[StudyCandidate] Deep-inspected candidates (`maf_files_parsed` populated). require_maf : bool True: keep only studies that…

### `find_datasets(query: 'str' = '', *, profile: 'RequirementProfile | None' = None, max_candidates: 'int' = 100, deep_inspect_top: 'int' = 10, max_workers: 'int' = 10, min_survivors: 'int | None' = None, organism: 'str | list[str] | None' = None, technique: 'str | list[str] | None' = None, sample_type: 'str | list[str] | None' = None, min_samples: 'int | None' = None) -> 'ComparisonReport'`

End-to-end discovery, deterministic when `profile` is structured. Pipeline (no LLM involved): 1. Push hard requirements into the search API (server-side filter). 2. Screen the shallow results: drop hard-fails on search-index data, rank survivors by a deterministic soft score. 3. Deep-inspect only the top `deep_inspect_top` survivors — the slow network step runs on a far smaller, already-qualified…

### `format_summary(candidate: 'StudyCandidate') -> 'dict[str, int]'`

Count data files by format, from a recursive FILES/ listing. A quick probe for "does this study have mzML / RAW / .d?" without downloading anything. Categories reflect the directory (RAW_FILES vs DERIVED_FILES) when available.

### `inspect_studies(candidates: 'list[StudyCandidate]', max_workers: 'int' = 10, tmp_dir: 'str | None' = None, download_data_files: 'bool' = False) -> 'list[StudyCandidate]'`

Deep-inspect candidates in parallel. For each candidate: 1. Download ISA-Tab metadata files from the MetaboLights FTP server 2. Parse the investigation file → assays, protocols, publications 3. Parse assay & sample files → sample count, characteristics, data files 4. Enrich the candidate with all discovered information Parameters ---------- candidates : list[StudyCandidate] Shallow candidates…

### `list_data_files(candidate: 'StudyCandidate') -> 'list[DataFileRef]'`

List data files recursively through FILES/ and its subdirectories. Some studies organize data in FILES/RAW_FILES/, FILES/DERIVED_FILES/, etc. Walks the HTTP directory tree, parsing filenames + sizes from HTML tables.

### `load_samples(task: 'SampleTask') -> 'list[SampleDescription] | None'`

Return cached descriptions for this task, or None if not cached.

### `load_study_from_isa(study_id: 'str', isa_dir: 'str | Path') -> 'StudyCandidate'`

Reconstruct a deep-inspected StudyCandidate from LOCAL ISA files. Offline / reuse path: if ISA-Tab files (i_*.txt, s_*.txt, a_*.txt, m_*.tsv) are already on disk, parse them without any network access — the same parsers used by :func:`inspect_studies`. Parameters ---------- study_id : str MetaboLights accession (e.g. `"MTBLS1375"`). isa_dir : str | Path Directory containing the ISA metadata…

### `parse_study_profile(llm_json: 'str') -> 'StudyProfile'`

Parse the LLM's JSON response into a :class:`StudyProfile`. Tolerates fenced/marked code blocks.

### `prepare_samples(candidate: 'StudyCandidate', store: 'SampleSentencesStore | None' = None, revision: 'int' = 0) -> 'SampleTask'`

Bundle everything for one study: contexts + the single LLM prompt. `revision` feeds the cache key: author a revised profile under a new revision so the feedback loop can compare wordings without clobbering.

### `profile_to_search_args(profile) -> 'dict'`

Deterministically map a profile's HARD requirements to search kwargs, so the search API pre-filters (server-side) instead of fetching everything. Only criteria the API/filter can express are mapped: organism, technique, sample_type, min_samples. Everything else is enforced later (shallow or deep screening).

### `render_maf_summary(analyses: 'list[MafAnalysis]') -> 'str'`

Collapse MAF analyses into one paste-ready text block. `""` when there are no analyses; otherwise one `summary` line per file.

### `revise_samples(task: 'SampleTask', profile_json: 'str') -> 'SampleTask'`

Author a revised wording under the next revision and cache it. Returns the *new* task (already submitted). Read results with `load_samples(new_task)`. Old wording remains under the previous revision, so the user can compare without clobbering.

### `score_studies(candidates: 'list[StudyCandidate]', profile: 'RequirementProfile') -> 'list[ScoredCandidate]'`

Score each candidate against the requirement profile. Returns scored candidates sorted descending by overall score. Candidates that fail a hard requirement are at the bottom (score=0).

### `screen_candidates(candidates: 'list[StudyCandidate]', profile: 'RequirementProfile', min_survivors: 'int' = 10) -> 'ScreeningResult'`

Deterministic pre-screen on search-index data (no LLM, no download). Drops candidates that fail shallow-checkable hard constraints, ranks the rest by a shallow soft score, and keeps at least `min_survivors` (or the total that pass) for deep inspection. Deterministic and cheap: only fields already present in the search hit.

### `search_studies(query: 'str' = '', *, page_size: 'int' = 100, filters: 'list[dict[str, Any]] | None' = None, sort_field: 'str | None' = None, sort_direction: 'str' = 'desc', ms_filters: 'dict[str, Any] | None' = None, organism: 'str | list[str] | None' = None, technique: 'str | list[str] | None' = None, sample_type: 'str | list[str] | None' = None, min_samples: 'int | None' = None, min_raw_files: 'int | None' = None, max_results: 'int' = 200) -> 'list[StudyCandidate]'`

Broad search returning shallow `StudyCandidate` objects. This is **Phase 1** — get many candidates quickly from the search index. Results contain search-level metadata (organisms, techniques, sample count, file counts, description, publications …) but **not** full ISA-Tab detail. Parameters ---------- query : str Free-text search (e.g. `"lipidomics human blood plasma"`). page_size : int Results…

### `start_download(candidate, config=None)`



### `submit_samples(task: 'SampleTask', profile_json: 'str') -> 'list[SampleDescription]'`

Apply the LLM-authored profile to every sample; cache and return. `profile_json` is the raw text the agent's LLM produced in answer to `task.profile_prompt`.

## Classes / constructors

### `DownloadConfig(file_types: 'list[str] | None' = None, sample_names: 'list[str] | None' = None, categories: 'list[str] | None' = None, dest_dir: 'str | None' = None, max_files: 'int | None' = None, max_size_gb: 'float | None' = None, parallel_downloads: 'int' = 4) -> None`

DownloadConfig(file_types: 'list[str] | None' = None, sample_names: 'list[str] | None' = None, categories: 'list[str] | None' = None, dest_dir: 'str | None' = None, max_files: 'int | None' = None, max_size_gb: 'float | None' = None, parallel_downloads: 'int' = 4)

- `file_types`: `list[str] | None`
- `sample_names`: `list[str] | None`
- `categories`: `list[str] | None`
- `dest_dir`: `str | None`
- `max_files`: `int | None`
- `max_size_gb`: `float | None`
- `parallel_downloads`: `int`

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

### `StudyCandidate(study_id: 'str', title: 'str' = '', description: 'str' = '', status: 'str' = '', organisms: 'list[OntologyTerm]' = <factory>, organism_parts: 'list[OntologyTerm]' = <factory>, assay_techniques: 'list[dict]' = <factory>, design_descriptors: 'list[OntologyTerm]' = <factory>, technology_types: 'list[OntologyTerm]' = <factory>, factors: 'list[OntologyTerm]' = <factory>, sample_count: 'int | None' = None, raw_file_count: 'int | None' = None, derived_file_count: 'int | None' = None, assay_count: 'int | None' = None, size_in_bytes: 'int | None' = None, publications: 'list[PublicationInfo]' = <factory>, contacts: 'list[str]' = <factory>, submitters: 'list[str]' = <factory>, submission_date: 'str' = '', public_release_date: 'str' = '', assays: 'list[AssayInfo]' = <factory>, data_files: 'list[DataFileInfo]' = <factory>, protocols: 'list[ProtocolInfo]' = <factory>, sample_metadata_fields: 'list[str]' = <factory>, sample_metadata: 'list[dict[str, str]]' = <factory>, metabolite_count: 'int | None' = None, metadata_completeness: 'float' = 0.0, investigation_file_parsed: 'bool' = False, assay_files_parsed: 'bool' = False, sample_file_parsed: 'bool' = False, maf_files_parsed: 'bool' = False, sample_file_map: 'dict[str, dict[str, list[str]]]' = <factory>, _raw_api_result: 'dict[str, Any]' = <factory>) -> None`

All known information about a MetaboLights study. Phase 1 (shallow) fields come from the search API. Phase 2 (deep) fields are populated after downloading + parsing ISA files.

- `study_id`: `str`
- `title`: `str`
- `description`: `str`
- `status`: `str`
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

### `StudyRequirements(organisms: 'list[str] | None' = None, sample_types: 'list[str] | None' = None, techniques: 'list[str] | None' = None, ionization_modes: 'list[str] | None' = None, analysis_types: 'list[str] | None' = None, instrument_models: 'list[str] | None' = None, data_formats: 'list[str] | None' = None, min_samples: 'int | None' = None, has_raw_data: 'bool | None' = None, has_derived_data: 'bool | None' = None, has_maf: 'bool | None' = None, min_metabolites: 'int | None' = None) -> None`

A set of requirements (used for both hard and nice-to-have).

- `organisms`: `list[str] | None`
- `sample_types`: `list[str] | None`
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
