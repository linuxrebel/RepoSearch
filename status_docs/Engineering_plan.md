# repo-browser — Engineering Plan

## Overview

repo-browser is a local-only searchable index of git repositories. It scans a directory tree of cloned repos, extracts metadata, auto-generates tags, builds semantic embeddings, and serves a web UI for searching by keyword, tag, or natural language.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ scan_repos.py│────▶│   repos.db   │◀────│embed_repos.py│
│  (scanner)   │     │  (SQLite)    │     │  (Ollama)    │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                     ┌──────▼───────┐
                     │repo_search.py│
                     │ (HTTP :8642) │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │  index.html  │
                     │  (browser)   │
                     └──────────────┘
```

All components are stdlib Python — zero pip dependencies. Ollama is the only external service (for embeddings via nomic-embed-text).

## Components

### scan_repos.py — Repository Scanner

Walks `gitParent` (from config), finds git repos up to 3 levels deep. For each repo:

1. Extracts: name, path, remote URL, default branch, last commit date
2. Parses README (first meaningful line → description, first 2000 chars → snippet)
3. Generates tags from:
   - File extension frequency (top 5 languages with ≥2 files)
   - README keyword matching (~80 infra/tool terms, whole-word regex)
   - Parent directory name (category tag)
   - Special file detection (Dockerfile, Jenkinsfile, ansible.cfg, etc.)
4. Deduplicates by normalised remote URL (git@↔https, strips .git)
   - Same URL in multiple folders → keeps deepest path
   - No-URL repos deduped by name
5. Cleans stale entries from all tables (repos removed or deduped)
6. Upserts into SQLite — idempotent, safe to rerun

Tags with `source='auto'` are regenerated every scan. Tags with `source='manual'` are preserved.

### embed_repos.py — Embedding Generator

Reads repos from DB, builds a text blob per repo (name + description + tags + README snippet truncated to 500 chars), sends to Ollama `nomic-embed-text`, stores the resulting float32 vector as a BLOB in `repo_embeddings`.

Only re-embeds repos where `repos.updated_at > repo_embeddings.updated_at` or no embedding exists. Commits in batches of 25.

### repo_search.py — HTTP Server + Search Engine

stdlib `http.server` on port 8642. Serves the UI and exposes a JSON API.

**Search algorithm (merged_search):**

1. **FTS5 keyword search** — phrase match first, then OR-token fallback. Ranks normalised to 0..1.
2. **Semantic search** — query embedded via Ollama, cosine similarity against all repo vectors. Floor threshold 0.30 (below that, not even considered).
3. **Name-match boost** — exact match → 1.0, substring → 0.7, token overlap → proportional.
4. **Tag-match boost** — query matches an existing tag exactly → 1.0, substring → 0.5. Also pulls in repos with matching tags that weren't in FTS/semantic results.
5. **Merge** — weighted blend: `0.2 × FTS + 0.3 × semantic + 0.2 × name + 0.3 × tag`
6. **Noise filter** — semantic-only results (no keyword, name, or tag signal) must exceed 0.65 cosine similarity to appear.

**API endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/search?q=` | GET | Combined search (empty q returns all repos) |
| `/api/tags` | GET | All unique tags with counts |
| `/api/stats` | GET | Repo/embedded/tag counts |
| `/api/config` | GET | Current config values and source |
| `/api/config` | POST | Save config to workDir/rb.config |
| `/api/browse?path=` | GET | Directory listing for path browser |
| `/` | GET | Serves index.html |

Auto-creates DB schema on first request (handles fresh install).

### index.html — Frontend UI

Single-page app, no build step, no dependencies. Features:

- Search bar with 250ms debounce
- Three search modes: both / keyword / semantic (toggle buttons)
- Tag cloud (filterable, tags with count ≥ 3)
- Repo cards: name (linked to remote), path, description, tags, branch, last commit date
- Score badges (green ≥50%, orange ≥25%, grey below)
- Clickable tags → search by tag
- Settings modal (gear icon): gitParent/workDir inputs with directory browser
- Saves config locally, shows `sudo cp` instruction

### find-dupe.py — Duplicate Clone Reporter

Scans `gitParent` independently (doesn't use DB), groups by normalised URL, outputs `~/Clone-Duplist.txt` with aligned columns showing app name and locations (directory names relative to gitParent). Supports repos in 2+ locations.

### rb_config.py — Shared Config Loader

Checks `/etc/rb.config` first, falls back to `rb.config` in script directory. Exports `get_git_root()`, `get_work_dir()`, `get_db_path()`, `get_config_source()`, `load_config()`. Used by all Python scripts.

### repo-browser.sh — CLI Wrapper

Reads config, manages server PID via `/tmp/repo-browser.pid`. Commands: start, stop, restart, status, rescan, duplist. Kills stale processes on port 8642 before starting.

## Data Model

```sql
repos (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    url TEXT,                    -- remote origin URL
    description TEXT,            -- first meaningful README line
    readme_snippet TEXT,         -- first 2000 chars of README
    last_commit TEXT,            -- ISO datetime of last commit
    default_branch TEXT,
    created_at TEXT,
    updated_at TEXT
)

repo_tags (
    repo_id INTEGER FK,
    tag TEXT,
    source TEXT DEFAULT 'auto',  -- 'auto' or 'manual'
    UNIQUE(repo_id, tag)
)

repo_embeddings (
    repo_id INTEGER PK FK,
    embedding BLOB,              -- float32 vector (768 dims for nomic)
    model TEXT,                  -- 'nomic-embed-text'
    updated_at TEXT
)

repo_fts USING fts5 (           -- contentless FTS5
    name, description, readme_snippet, tags,
    content='', content_rowid='rowid'
)

scan_log (
    id INTEGER PRIMARY KEY,
    scanned_at TEXT,
    repos_found INTEGER,
    repos_added INTEGER,
    repos_updated INTEGER
)
```

## File Layout

```
repo-browser/
├── repo-browser.sh        # CLI wrapper (symlink into PATH)
├── scan_repos.py          # scanner
├── embed_repos.py         # embedding generator
├── repo_search.py         # HTTP server + search
├── find-dupe.py           # duplicate reporter
├── rb_config.py           # config loader
├── index.html             # frontend UI
├── rb.config.example      # config template
├── requirements.txt       # documents deps (stdlib only)
├── README.md
├── status_docs/
│   ├── Engineering_plan.md
│   └── Project_state.md
├── .gitignore
└── repos.db               # (gitignored, generated)
```

## Dependencies

- Python 3.10+ (stdlib only: http.server, sqlite3, subprocess, json, struct, math, urllib, re, os, pathlib)
- Ollama running locally with `nomic-embed-text` model pulled
- No pip installs, no Node.js, no build tools

## Config

`/etc/rb.config` (or local `rb.config`):
```
gitParent=/path/to/git/repos
workDir=/path/to/repo-browser
```

## Key Design Decisions

1. **stdlib only** — no pip deps means no venv, no version conflicts, portable across any Python 3.10+ system
2. **SQLite** — single-file DB, CLI-queryable, FTS5 built-in, portable (copy one file)
3. **Contentless FTS5** — smaller footprint, but requires special delete syntax (`INSERT INTO fts(fts, rowid, ...) VALUES('delete', ...)`)
4. **Embeddings as BLOBs** — avoids sqlite-vec dependency, cosine sim computed in Python (fast enough for <1000 repos)
5. **Dedup by URL** — same repo in multiple category folders is common; keeps deepest path as the canonical location
6. **Tag-match boost in search** — prevents noisy semantic results from drowning out obvious matches when searching a known tag
7. **Semantic noise floor at 0.65** — nomic-embed-text produces high baseline similarities (~0.55-0.60 for unrelated text); 0.65 filters most noise without losing real matches
