# Porting DocuBrowser features into repo-browser

repo-browser is the ancestor; DocuBrowser (`/home/james/git/AI/DocuBrowse`) forked
from the same DNA (Ollama `nomic-embed-text`, SQLite FTS5 + embeddings, single-file
HTTP server, dark/light UI) and got ~6 months more hardening. This tracks what
DocuBrowser grew that repo-browser should adopt, so we don't re-derive it each time.

Status legend: ✅ done · ⬜ not started · 🔶 partial

---

## Tier 1 — bugs DocuBrowser already fixed, repo-browser still had

| Item | Status | Where in repo-browser | DocuBrowser source |
|---|---|---|---|
| Search-on-Enter (kill as-you-type debounce) | ✅ | `index.html` search handler | search fires on Enter; debounce reverted (partial-word searches hurt typing) |
| Stopword stripping before semantic embed | ✅ | `repo_search.py` `merged_search` | `deep_links.py` `strip_stopwords` (articles + FANBOYS) |
| Host-header allowlist (anti-DNS-rebind) | ✅ | `repo_search.py` `_host_allowed` | `doc_search.py` `_host_allowed` / `_hostname_allowed` |
| CSRF token on mutating POST | ✅ | `repo_search.py` `_guard_mutation` + `index.html` | `doc_search.py` `_guard_mutation`, per-process `secrets` token, meta-tag inject |

**Ported minimal**, not wholesale: DocuBrowser's security also carries
`DOCUBROWSE_TRUSTED_CIDRS` / BFF-proxy / container-service-name machinery. repo-browser
is loopback-only, single-user, no container story — so we ported only the loopback
host check + per-process CSRF token. Add the CIDR/BFF layer only if repo-browser ever
ships in a container.

## Tier 2 — search quality (not started)

- ⬜ **bm25 column weights** — DocuBrowser weights FTS columns (name/title/tags above
  content body). repo-browser uses flat rank normalization (`repo_search.py` `fts_search`
  → `merged_search`). Name hits should outrank README-body hits.
- ⬜ **Prefix-OR MATCH** — DocuBrowser builds `"tok"*` prefix queries so partial words
  match. repo-browser does exact-phrase then plain OR tokens, no wildcards.
- ⬜ **Hybrid scoring** — DocuBrowser `max(fts,sem)+0.1·min(fts,sem)`, calibrated/tested.
  repo-browser fixed blend `0.2·fts+0.3·sem+0.2·name+0.3·tag` (hand-tuned magic numbers).
- ⬜ **Semantic floor** — DocuBrowser `_SEMANTIC_MIN_SIM=0.5` (nomic: real ~0.6-0.7,
  noise ~0.45). repo-browser mixes 0.30 threshold + 0.65 semantic-only floor. Align.

## Tier 3 — performance (not started, lower priority: repo count « doc corpus)

- ⬜ **Parallel embed** — repo-browser embeds serial, one Ollama round-trip at a time
  (`embed_repos.py`). DocuBrowser: ThreadPool → Ollama.
- ⬜ **Hardware-aware workers** — DocuBrowser `hardware_utils.py` scales worker count to
  CPU/GPU/RAM. repo-browser: none.

## Tier 4 — capability, more work (not started)

- ⬜ **Deep Links** (`deep_links.py`) — jump to the passage inside a doc. For repos =
  jump to README location. Low value (READMEs short), high port cost.
- ⬜ **LLM synopsis** (dolphin3) — DocuBrowser generates AI summary. repo-browser has
  `summary` = first README paragraph, heuristic-extracted (`scan_repos.py`
  `extract_summary`). Cheaper, arguably fine.
- ⬜ **Cross-platform paths** (`platform_paths.py`) — XDG / macOS / Windows. repo-browser
  is `/etc/rb.config` + workDir, WSL-only for Windows.
- ⬜ **Packaging** — DocuBrowser ships rpm/deb/tar/win/mac. repo-browser: tarball only.
