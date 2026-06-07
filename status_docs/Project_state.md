# repo-browser — Project State

**Last updated:** 2026-06-06
**Location:** `/home/james/bin/repo-browser/`
**Branch:** `main`
**Owner:** James Sparenberg (linuxrebel)

## Current State: Functional MVP

The project is usable and running in production on Bairn (Fedora 44). All core features work. No remote/GitHub repo yet — local git only.

## What Works

- **Scanner**: Finds 419 unique repos (from 453 total, 34 deduped) under `/home/james/git`
- **Tag generation**: 100% coverage, avg 5.8 tags/repo, 129 unique tags
- **Embeddings**: All 419 repos embedded via `nomic-embed-text` (~18 seconds)
- **Search**: FTS5 keyword + semantic cosine similarity + name-match + tag-match boost
- **UI**: Dark-themed single-page app at http://localhost:8642
- **Settings**: Gear icon → config modal with directory browser
- **Config**: `/etc/rb.config` with fallback to local, fresh install starts without config
- **CLI**: `repo-browser.sh {start|stop|restart|status|rescan|duplist}`
- **Dedup**: URL-based dedup in scanner, stale cleanup on rescan
- **Dupe reporter**: `duplist` command → `~/Clone-Duplist.txt`

## Git Log (as of last session)

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

## Bugs Fixed During Development

1. **MonVisor-Corpus not found** — FTS normalization was wrong; fixed with phrase-match + OR-token fallback and name-match boost
2. **Semantic noise** — nomic-embed-text has high baseline similarity (~0.55-0.60); added 0.65 floor for semantic-only results
3. **Untagged repos in tag searches** — searching "Rust" returned non-Rust repos; fixed with tag-match boost (30% weight)
4. **Duplicates in results** — same repo in multiple folders; fixed with URL-based dedup in scanner
5. **FTS5 contentless delete** — `DELETE FROM fts` doesn't work on contentless tables; fixed with special `INSERT ... VALUES('delete', ...)` syntax
6. **Port 8642 in use on restart** — added `HTTPServer.allow_reuse_address = True` and stale PID kill in wrapper
7. **Config chicken-and-egg** — can't configure without running, can't run without config; fixed by starting server with defaults and serving settings UI
8. **Wrapper script path resolution** — `SCRIPT_DIR/repo-browser/rb.config` double-nested; fixed to `SCRIPT_DIR/rb.config`

## Known Limitations / Future Work

### Search Quality
- **Semantic threshold is static** — 0.65 cosine floor works for nomic-embed-text but would need tuning if the model changes
- **No typo tolerance in FTS5** — SQLite FTS5 doesn't do fuzzy matching natively; typos in keyword mode get no results. Semantic mode covers this partially
- **Tag quality varies** — auto-generated tags from file extensions can be noisy (yaml, json, toml appear on nearly everything). Could weight by relevance or add a stoplist
- **README parsing is naive** — first meaningful line as description works ~80% of the time; some repos have badges or HTML that leaks through

### Features Not Yet Built
- **Manual tag editing via UI** — schema supports `source='manual'` but no UI to add/remove tags
- **Repo detail view** — clicking a card could show full README, all tags, commit history
- **Batch operations** — select multiple repos for tagging, archiving, or deletion
- **gitUp integration** — scanner could auto-run after gitUp syncs repos (just append to gitUp script)
- **GitHub/GitLab API enrichment** — pull repo topics, stars, language breakdown from remote API
- **Export** — dump search results to CSV/JSON
- **systemd service** — run as a proper service instead of nohup background process
- **Remote push** — push to GitHub as a public/private repo

### Performance
- **Cosine similarity is brute-force** — loops all 419 embeddings per query (~50ms, fine for now). At 5000+ repos, consider sqlite-vec or FAISS
- **Embedding is serial** — one repo at a time. Could batch via Ollama's batch API
- **No caching** — every search re-queries DB and re-computes cosine sim. Could cache embeddings in memory on startup

### Packaging
- **No setup.py/pyproject.toml** — not installable via pip yet
- **No Tauri/Electron wrapper** — runs as a local web server only
- **No RPM/DEB** — could package for Fedora given James's RPM packaging background

## Environment

- **Host:** Bairn (Fedora 44, KDE Plasma)
- **Python:** 3.14
- **Ollama models:** gemma4:latest, nomic-embed-text:latest, openclaw:latest, dolphin-uncensored, dolphincoder:7b
- **GPU:** NVIDIA GeForce RTX 3050 4GB
- **RAM:** 31 GB
- **DB size:** ~2-3 MB for 419 repos with embeddings
- **Embedding model:** nomic-embed-text (768-dim vectors, 274 MB model)

## File Quick Reference

| File | Lines | Purpose |
|---|---|---|
| `scan_repos.py` | ~370 | Scanner + tag gen + dedup |
| `embed_repos.py` | ~100 | Ollama embedding generator |
| `repo_search.py` | ~400 | HTTP server + search engine |
| `find-dupe.py` | ~120 | Duplicate clone reporter |
| `index.html` | ~250 | Frontend UI |
| `rb_config.py` | ~30 | Config loader |
| `repo-browser.sh` | ~140 | CLI wrapper |

## How to Resume Work

1. Read this file and `Engineering_plan.md`
2. The repo is at `/home/james/bin/repo-browser/` on branch `main`
3. Config is at `/etc/rb.config` (gitParent=/home/james/git, workDir=/home/james/bin/repo-browser)
4. Start server: `repo-browser.sh start` → http://localhost:8642
5. Key files to understand the search: `repo_search.py` (merged_search function) and `scan_repos.py` (scan_and_populate function)
6. Filesystem MCP and Desktop Commander are available for file access on Bairn
7. Desktop Commander `start_process` output is contaminated by neofetch in shell profile — redirect output to files in `/home/james/` and read back
