# repo-browser — Project State

**Last updated:** 2026-06-06
**Location:** `/home/james/bin/repo-browser/`
**Branch:** `jdev` (pending merge to `main`)
**Owner:** James Sparenberg (linuxrebel)

## Current State: Functional MVP

The project is usable and running in production on Bairn (Fedora 44). All core features work. No remote/GitHub repo yet — local git only.

## What Works

- **Scanner**: Finds repos under configured `gitParent`; URL-based dedup, stale cleanup on rescan
- **Tag generation**: Auto-tags from file extensions, README keywords, and category folder name
- **Embeddings**: All repos embedded via `nomic-embed-text` (768-dim vectors)
- **Search**: FTS5 keyword + semantic cosine similarity + name-match + tag-match boost
- **UI**: Dark + light theme (toggle in header), single-page app at http://localhost:8642
- **Settings**: Gear icon → config modal with directory browser
- **Config**: `/etc/rb.config` with fallback to local, fresh install starts without config
- **CLI**: `repo-browser.py {start|stop|restart|status|rescan|duplist}` (cross-platform Python)
- **Dedup**: URL-based dedup in scanner; `duplist` reports groups, rescan reports copies removed
- **Dupe reporter**: `duplist` command → `~/Clone-Duplist.txt`
- **TLDR summaries**: Heuristic prose extraction from README, shown on each card
- **ADA compliant**: Light mode accent colors pass WCAG 2.1 AA (4.5:1 contrast)
- **macOS support**: `ensure_ollama.py` detects platform, uses `brew install ollama` on macOS
- **Empty DB ships**: `repos.db` is in `.gitignore`; schema created automatically on first run

## Git Log (jdev, ahead of main)

```
3322e0e feat: heuristic TLDR summary on repo cards
3b9d8bb remove: repo-browser.sh — superseded by repo-browser.py
ab0dfb0 fix: clarify dedup message — report group count and copy count separately
6ac1450 Replace bash launcher with repo-browser.py; macOS Ollama support; WSL note in README
```

## Full Git Log (main baseline)

```
8824f6a Rename --duplist to duplist for consistency
f1bce1e Rewrite README: Installation and Usage, PATH setup, full workflow
cd1a6cb Remove rb.config from tracking, add rb.config.example template
ae33a05 Fix port-in-use crash: SO_REUSEADDR + kill stale on start
9e423c7 Handle fresh install: auto-create DB schema on first request
055cf80 Fix config fallback: start without /etc/rb.config
628cd72 Add repo-browser.sh wrapper script
8cfa773 Update README with full docs
e751a36 Add config system and settings UI
b33dd47 Initial commit: repo-browser
```

## Bugs Fixed This Session

1. **duplist vs rescan count mismatch** — rescan reported N copies removed, duplist reported N groups. Now rescan says "X repos have duplicates (Y extra copies removed)" so both use the same group metric
2. **macOS `readlink -f`** — BSD `readlink` doesn't support `-f`; fixed by replacing bash launcher with `repo-browser.py` using `pathlib.Path.resolve()`
3. **README description quality** — first-line heuristic often returned the repo title or badge noise; replaced with `extract_summary()` that finds first real prose paragraph

## Known Limitations / Future Work

### Features Not Yet Built
- **Duplicate cleanup** — `duplist` reports duplicates but no `dupclean` command yet to remove/archive them
- **Manual tag editing via UI** — schema supports `source='manual'` but no UI to add/remove tags
- **Repo detail view** — clicking a card could show full README, all tags, commit history
- **Batch operations** — select multiple repos for tagging, archiving, or deletion
- **gitUp integration** — scanner could auto-run after gitUp syncs repos
- **GitHub/GitLab API enrichment** — pull repo topics, stars, language breakdown from remote API
- **Export** — dump search results to CSV/JSON
- **systemd service** — run as a proper service instead of background process
- **`reset` command** — delete DB and rescan in one shot for true corruption recovery

### Search Quality
- **Semantic threshold is static** — 0.65 cosine floor works for nomic-embed-text but needs tuning if model changes
- **No typo tolerance in FTS5** — semantic mode covers this partially
- **Tag quality varies** — yaml/json/toml appear on nearly everything; could add a stoplist
- **TLDR heuristic has limits** — repos with table-heavy or badge-only READMEs fall back to legacy description

### Performance
- **Cosine similarity is brute-force** — fine up to ~1000 repos; beyond that consider sqlite-vec or FAISS
- **Embedding is serial** — one repo at a time; could batch via Ollama's batch API
- **No caching** — every search re-queries DB and recomputes cosine sim

### Packaging
- **No setup.py/pyproject.toml** — not pip-installable yet
- **Windows not supported** — WSL required; noted in README

## Environment

- **Host:** Bairn (Fedora 44, KDE Plasma)
- **Python:** 3.14
- **Ollama models:** nomic-embed-text (required), generative model optional
- **GPU:** NVIDIA GeForce RTX 3050 4GB
- **RAM:** 31 GB
- **Embedding model:** nomic-embed-text (768-dim, 274 MB)

## File Quick Reference

| File | Purpose |
|---|---|
| `repo-browser.py` | CLI launcher (start/stop/restart/status/rescan/duplist) |
| `scan_repos.py` | Scanner + tag gen + dedup + TLDR summary extraction |
| `embed_repos.py` | Ollama embedding generator |
| `repo_search.py` | HTTP server + search engine |
| `find-dupe.py` | Duplicate clone reporter |
| `ensure_ollama.py` | Ollama + model dependency check/install (cross-platform) |
| `index.html` | Frontend UI (dark/light theme) |
| `rb_config.py` | Config loader |
| `rb.config.example` | Config template |

## How to Resume Work

1. Read this file and `Engineering_plan.md`
2. Repo is at `/home/james/bin/repo-browser/` on branch `jdev`
3. Config is at `/etc/rb.config` (gitParent=/home/james/git, workDir=/home/james/bin/repo-browser)
4. Start server: `repo-browser.py start` → http://localhost:8642
5. Key files: `repo_search.py` (merged_search), `scan_repos.py` (scan_and_populate, extract_summary)
6. Filesystem MCP and Desktop Commander available for file access on Bairn
7. Desktop Commander `start_process` output is contaminated by neofetch — redirect to files and read back
