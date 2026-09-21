# this project is a work in progress - no stability guarantees.
# please create an issue if you have one
# metabo-search — guide

Open **[`index.html`](index.html)** in any browser for the full, styled guide
(problem → solution → architecture → install → quick start → API → troubleshooting).

No build step; everything except the Mermaid workflow chart (jsdelivr CDN)
renders offline.

Other docs:
- [`../SKILL.md`](../SKILL.md) — the Agent-Skills-standard skill file (what pi/Claude/opencode load)
- [`../references/api.md`](../references/api.md) — full API reference
- [`../docs/cookbook.md`](cookbook.md) — run-tested example patterns (linted by the test suite)
- [`../docs/design-pipeline.md`](design-pipeline.md) — typed-pipeline design (steps, results, caching)
- [`../agents.md`](../agents.md) — maintenance/debug notes
- [`perf.md`](perf.md) — benchmark results + justification for the performance changes (keep-alive, process parsing, overlap, listing cache, by-value handoff)
- [`api.md`](api.md) — auto-generated signature reference (refresh: `scripts/python scripts/gen_api_docs.py`)

- Workflow chart: compact Mermaid `flowchart TD` (8 nodes, self-loop arcs) — fits on screen.
- No quick-start; has 4 rough API usage examples (discovery, sentences, download, offline/manifest).