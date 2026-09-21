# Performance — measured results & justification

Date: session of pipeline build + performance work.
Environment: 8-core macOS (arm64), Python 3.14, httpx 0.28.1, live MetaboLights
API (`ftp.ebi.ac.uk`, `www.ebi.ac.uk/metabolights`).

> ⚠️ **Read the confounds first.** Live numbers are subject to EBI latency.
> **Offline numbers (parse, cache) are precise; live wall-times are
> indicative, and all gains were re-confirmed on the final clean, guarded
> runs.**

---

## 1. What was added, and what each change justifies

| Change | Evidence | Justification |
|---|---|---|
| **Shared keep-alive `httpx.Client`** (inspector + downloader) | Metadata download, 36 studies @ 12 threads: **19.2s (0.53s/study)** after vs ~0.74–0.85s/study before (24 studies @ 8–12 workers) | `httpx.get()` opened a fresh TLS connection *per request* (~1 listing + 4–6 files per study). Hundreds of studies = hundreds of handshakes. One pooled client reuses connections across all requests/threads. Monotonic. |
| **Process-pool parsing** (`inspect(parse_workers=...)`, auto-on ≥8 studies) | Offline, 16 studies, 4 workers: threads **2.95s** vs processes **1.39s (2.1×)**; serial parse 181 ms/study; **threads give zero parse speedup** (GIL: 172 ms/study @ 4 threads ≈ serial) | The CPU phase was GIL-serialized. Processes run parse on real cores; identical results (determinism test: `test_process_parse_equals_threads`). |
| **Overlap parse under download** | Structural (parse now submits the moment each download lands) | Parse (~0.18s/study) was on the critical path after all downloads; overlap hides it under I/O — net wall = download time. |
| **FILES/ listing disk cache** (`list_data_files(cache_dir=)`, `DownloadConfig.files_cache_dir`) | Offline test: repeat `list_data_files` = 0 walks (HTTP walk once, then JSON); corrupt cache re-walks | Repeated `download()` runs in one cache root skip the recursive listing walk (tens of requests per study) entirely. |
| **By-value result handoff** (deep copy at every step boundary) | Live: cold search in-memory payload **924,882 bytes** vs the stored shallow **23,544 bytes** — `inspect`'s in-place merge mutated candidates still referenced by earlier results → warm digests ≠ fresh digests → cache silently re-ran | The step cache is keyed on input digests; reference bleed made warm replay unreliable. Copy-on-handoff makes results immutable across steps; regression `test_results_cross_steps_by_value`. |

Numbers were re-checked on the final clean runs (guarded scripts):

| Batch (live, cold) | Before (pre-keep-alive, same batch) | After | Gain |
|---|---|---|---|
| 5 studies, 4 workers | 3.46s smoke (5 studies, 4 workers) / 4.46s zoo | **2.44s (0.49s/study)** | **~1.4×** |
| 12 studies, 8 workers (process parse auto) | ~0.75–0.85s/study | **7.08s (0.59s/study)** | **~1.4–1.5×** |
| Warm re-run (any of the above) | (cache unreliable) | **0.13–0.19s** | **~15–30× vs cold** |

---

## 2. Options measured and rejected (with data)

| Option | Result | Decision |
|---|---|---|
| **HTTP/2** | 24 files @ 8 threads: **0.37s (h1.1) vs 0.83s (h2)** | Rejected — slower on EBI's front (ALPN/TLS overhead for tiny files). |
| **Async inspector** | 36 studies: sync 12 thr **19.2s** · async 16 **20.1s** · async 24 **18.8s**; ≥32 concurrent quickly refused (`All connection attempts failed`) | Rejected — server caps per-host concurrency ≈ 24; threads + keep-alive already reach it. Async adds complexity for no gain. |
| **REST-zip as primary ISA fetch** (1 request/study vs ~6) | `ws/studies/{id}/download/isa?format=zip` → **503**; ws3 variant → **404** | Rejected — zip service currently down; HTTP listing+files stays primary, REST remains fallback. Re-evaluate when back up. |

---

## 3. Caveats & contracts

- **Live numbers are noisy** (external latency). The project's own keep-alive
  + cache (TTLs: search 7d, inspect 30d) are what keep *repeats* cheap
  regardless of network state.
- **Process parsing requires the `__main__` guard** (macOS spawn re-imports the
  entry script into each worker). Unguarded scripts: children re-execute the
  whole module — seen live as ~1,000 simultaneous requests and garbage timings.
  Mitigations: guard the module, or `MTBLS_PARSE_PROCESSES=0` for ad-hoc
  scripts. pytest is fine.
- **Server overload leaves shallow candidates — and they are cached.** 16+
  workers measured-refusals on the file server (≈24-conn host cap); studies
  come back **shallow** (soft fail, no crash), and that failed deep result is
  cached for 30d, silently replaying on re-runs. Recovery: per-repo
  `workers≈8`, thread parsing (`parse_workers=0`), serial `workers=1` retries
  with backoff + `force=True`. Detection: `inspection_depth != "deep"`.
---

## 4. Where the remaining time goes (projections, not measurements)

Per-study cold budget at 8 workers ≈ 0.5–0.6s ≈ mostly network (listing +
~4–6 tiny files); parse has been removed from the critical path. For
hundreds of studies the first-run cost scales with that network throughput;
re-runs are ~0. Revisit REST-zip-primary (if EBI restores it) as the next
real lever.