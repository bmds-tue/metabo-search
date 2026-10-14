# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-14

First release. Two independent, strictly typed clients over the MetaboLights
and Metabolomics Workbench web APIs, plus an optional DuckDB working set built
from search results. The repositories are never unified.

### Added

- **MetaboLights client** — faceted study search, point lookups, deep ISA + MAF
  inspection (samples and metabolites), `FILES/` listing, and selective
  downloads. Best-effort `inspect()` collects per-study errors instead of
  aborting a batch.
- **Metabolomics Workbench client** — whole-index corpus search (cached on
  disk, 7-day TTL), per-study factors/analysis/metabolites, and partial
  `inspect()` that only fetches the requested parts.
- **DuckDB working set** (`Workspace`) — per-source tables (`ml_*` / `wb_*`),
  idempotent upsert on natural keys, Arrow bulk loading, and an ingest audit
  trail. Shipped as the optional `ingest` extra.
- **Shared transport** — connection pooling, retries with exponential backoff,
  `Retry-After` handling, an optional token-bucket rate limit, typed errors, and
  cumulative attempt/throttle statistics.
- **`Results[T]`** — a `list` subclass with `.ids()`, `.records()`, and `.fmt()`.
- Strict typing (`py.typed`) and a fast offline test suite.

### Notes

- Search the web APIs, ingest only the hits you care about, then analyse with
  SQL. The repositories are never mirrored.
- MIT licensed.
