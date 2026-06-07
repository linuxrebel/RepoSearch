# repo-browser — Project State

**Last updated:** 2026-06-06
**Location:** `/home/james/bin/repo-browser/`
**Branch:** `jdev` (ahead of main — pending PR merge)
**Owner:** James Sparenberg (linuxrebel)

## Current State: Functional MVP

Running in production on Bairn (Fedora 44). All core features work. No remote/GitHub repo yet — local git only.

## What Works

- **Scanner**: Finds repos under configured `gitParent`; URL-based dedup, stale cleanup on rescan
- **Tag generation**: Auto-tags from file extensions, README keywords, and category folder name
- **Embeddings**: All repos embedded via `nomic-embed-text` (768-dim vectors)
- **Search**: FTS5 keyword + semantic cosine similarity + name-match + tag-match boost
- **UI**: Dark + light theme (toggle in header), single-page app at http://localhost:8642
- **Settings**: Gear icon → config modal with directory browser
- **Config**: `/etc/rb.config` with fallback to local; fresh install starts without config
- **CLI**: `repo-browser.py {start|stop|restart|status|rescan|duplist|dupclean}` (cross-platform Python)
- **Dedup**: URL-based dedup in scanner; `duplist` reports groups; rescan reports copies removed
- **dupclean**: Curses TUI — walks duplicate groups one at a time; S skips, Q/Ctrl-C exits cleanly
- **TLDR summaries**: Heuristic prose extraction from README, shown on each card
- **ADA compliant**: Light mode accent colors pass WCAG 2.1 AA (4.5:1 contrast)
- **macOS support**: `ensure_ollama.py` detects platform, uses `brew install ollama` on macOS
- **Empty DB ships**: `repos.db` is in `.gitignore`; schema created automatically on first run
- **Corruption recovery**: Rescan re-creates any missing tables/columns via `ALTER TABLE` migration

## Git Log (jdev, ahead of main)

```
ccbb075 fix: S key skips without delete dialog; Ctrl-C exits cleanly
b387970 fix: clean Ctrl-C exit during deletion prompts
a405355 refactor: dupclean processes one repo at a time; clean Ctrl-C exit
06643f5 docs: update screenshots
04b1484 refactor: dupclean TUI shows one repo at a time
4573c44 feat: dupclean — interactive TUI for duplicate repo cleanup
c7c6dd0 docs: remove repo-browser.sh refs; Windows ⚠️ not ❌
362ba97 docs: update Project_state.md for jdev session
3322e0e feat: heuristic TLDR summary on repo cards
3b9d8bb remove: repo-browser.sh — superseded by repo-browser.py
ab0dfb0 fix: clarify dedup message — report group count and copy count separately
6ac1450 Replace bash launcher with repo-browser.py; macOS Ollama support; WSL note in README
```

## Bugs Fixed (recent sessions)

1. **duplist vs rescan count mismatch** — rescan reported N copies removed, duplist reported N groups. Now rescan says "X repos have duplicates (Y extra copies removed)"
2. **macOS `readlink -f`** — BSD readlink doesn't support `-f`; fixed by replacing bash launcher with `repo-browser.py` using `pathlib.Path.resolve()`
3. **README description quality** — first-line heuristic returned titles or badge noise; replaced with `extract_summary()` that finds first real prose paragraph
4. **dupclean S-key** — pressing S still showed the delete dialog; fixed with `confirmed` flag before selection `while` loop
5. **dupclean Ctrl-C traceback** — `endwin() returned ERR` from `curses.wrapper`; fixed by calling `stdscr.refresh()` before any `return` from `_tui_main` when curses has been temporarily ended

## Known Limitations / Future Work

### Features Not Yet Built
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
- **Windows not supported natively** — WSL required; noted in README

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
| `repo-browser.py` | CLI launcher (start/stop/restart/status/rescan/duplist/dupclean) |
| `scan_repos.py` | Scanner + tag gen + dedup + TLDR summary extraction |
| `embed_repos.py` | Ollama embedding generator |
| `repo_search.py` | HTTP server + search engine |
| `find-dupe.py` | Duplicate clone reporter (writes `~/Clone-Duplist.txt`) |
| `dupe_clean.py` | Curses TUI for interactive duplicate cleanup |
| `ensure_ollama.py` | Ollama + model dependency check/install (cross-platform) |
| `index.html` | Frontend UI (dark/light theme) |
| `rb_config.py` | Config loader |
| `rb.config.example` | Config template |

## How to Resume Work

1. Read this file and `Engineering_plan.md`
2. Repo is at `/home/james/bin/repo-browser/` on branch `jdev`
3. Config is at `/etc/rb.config` (gitParent=/home/james/git, workDir=/home/james/bin/repo-browser)
4. Start server: `repo-browser.py start` → http://localhost:8642
5. Key files: `repo_search.py` (merged_search), `scan_repos.py` (scan_and_populate, extract_summary), `dupe_clean.py` (_tui_main)
6. Filesystem MCP and Desktop Commander available for file access on Bairn
7. Desktop Commander `start_process` output is contaminated by neofetch — redirect to files and read back
8. **Branch rule:** all changes on `jdev` — James merges to main
