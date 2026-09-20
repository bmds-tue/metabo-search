# metabo-search — guide

Open **[`index.html`](index.html)** in any browser for the full, styled guide
(problem → solution → architecture → install → quick start → API → troubleshooting).

Self-contained: no build step, no CDNs, works offline.

Other docs:
- [`../SKILL.md`](../SKILL.md) — the Agent-Skills-standard skill file (what pi/Claude/opencode load)
- [`../references/api.md`](../references/api.md) — full API reference
- [`../agents.md`](../agents.md) — maintenance/debug notes
- [`perf.md`](perf.md) — benchmark results + justification for the performance changes (keep-alive, process parsing, overlap, listing cache, by-value handoff)

- Workflow chart: compact Mermaid `flowchart TD` (8 nodes, self-loop arcs) — fits on screen.
- No quick-start; has 4 rough API usage examples (discovery, sentences, download, offline/manifest).
