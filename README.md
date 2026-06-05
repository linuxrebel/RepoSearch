# repo-browser

Local searchable index of all git repos in `/home/james/git`.

## Files

| File | Purpose |
|---|---|
| `scan_repos.py` | Walks git dir, extracts metadata + auto-generates tags, populates SQLite |
| `embed_repos.py` | Generates semantic embeddings via Ollama nomic-embed-text |
| `repo_search.py` | Local web server with search UI (FTS5 + cosine similarity) |
| `repos.db` | SQLite database (the whole state — portable) |
| `index.html` | Frontend UI |

## Usage

```bash
# 1. Scan repos (rerun anytime, idempotent)
python3 scan_repos.py

# 2. Generate embeddings (requires Ollama running)
python3 embed_repos.py

# 3. Start search server
python3 repo_search.py
# → http://localhost:8642
```

## Search modes

- **keyword** — FTS5 full-text search, typo-tolerant via SQLite
- **semantic** — cosine similarity on nomic-embed-text vectors via Ollama
- **both** (default) — weighted blend: 40% keyword + 60% semantic

## Dependencies

- Python 3.10+ (stdlib only, no pip installs)
- Ollama with `nomic-embed-text` pulled (for embeddings)

## Portability

Copy `repos.db`, the 3 Python files, and `index.html` to a new machine. Rerun `scan_repos.py` to update paths.
