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

## Tier 2 — search quality ✅ (all four, in `repo_search.py`)

- ✅ **bm25 column weights** — `fts_search` now ranks by `bm25(repo_fts, 8, 3, 3, 5)`
  (name/description/readme_snippet/tags), so name and tag hits outrank README body.
  This subsumes the old separate `name_match_score` + tag-boost logic (both deleted).
- ✅ **Prefix-OR MATCH** — `build_fts_expr` builds `"tok"*` prefix-OR over bare words
  (each FTS5-quoted to neutralize operators); double-quoted input stays a phrase literal.
- ✅ **Hybrid scoring** — `merged_search` collapsed to DocuBrowser's 2-signal blend
  `min(max(fts,sem)+0.1·min(fts,sem), 1.0)`; the hand-tuned 4-term weighted blend is gone.
- ✅ **Semantic floor** — both-mode gate is `keyword hit OR sem≥0.30` (matches
  DocuBrowser's **main-search** floor). NOTE: the earlier `0.5` in this doc was
  DocuBrowser's Deep Links `_SEMANTIC_MIN_SIM`, a different knob — main search uses 0.30.
  Dropped repo-browser's old 0.65 semantic-only special case.

Behavior note: bm25 scores normalize so the best keyword hit = 1.0, and the `+0.1·min`
boost clamps at 1.0, so several strong top hits can pin at 1.000 (same as DocuBrowser).

### Open tuning: "both" mode is too loose — TIGHTEN AFTER INTEGRATION

Reported 2026-08-27. Symptom: searching `docubrowser` in **both** mode returns the two
real DocuBrowser repos at #1/#2 (good), then a tail of totally unrelated repos.
**keyword** mode is correctly tight (only `DocuBrowse-Ent`).

Diagnosis: the tail is semantic-only matches clearing the `sem≥0.30` gate. 0.30 is
DocuBrowser's floor, tuned for a large prose document corpus where 0.30 noise is buried.
repo-browser has far fewer repos and short metadata (name + one-line desc + README
snippet + tags), so a 0.30 semantic-only match is visibly irrelevant. **The 0.65
semantic-only floor we deleted in this port was doing real work here.**

repo-browser must be tighter than DocuBrowser — do NOT just inherit its floor.

Candidate fix (not yet done — hold until the port is finished):
- Keep `0.30` only when a repo ALSO has a keyword hit (the both-boost case).
- For semantic-only repos (no keyword hit), require a much higher floor (~0.55–0.65)
  before they enter "both" results. Essentially a return of the old special case, but
  layered onto the new 2-signal `merged_search`.
- Calibrate the exact floor against real queries (`docubrowser`, single-word names).
- Consider whether "both" should rank keyword hits strictly above semantic-only hits
  regardless of raw score, so a weak name match never sits below a fuzzy semantic one.

Also seen in the same report: keyword `docubrowser` did NOT surface the repo literally
named `DocuBrowse` (no trailing "r") — only `DocuBrowse-Ent`. Prefix match is directional:
`"docubrowser"*` matches tokens STARTING with "docubrowser", but the token is the shorter
`docubrowse`, so it misses. The query typed more than the token. Worth handling
symmetric near-matches (e.g. also try the query as a prefix target, or fuzzy/trigram on
names) when we tighten — recall and precision need fixing together, not just the floor.

## Tier 3 — performance ✅ (scoped to what fits repo-browser)

- ✅ **Parallel embed** — `embed_repos.py` `embed_all` now runs Ollama calls in a
  `ThreadPoolExecutor`; all DB writes stay on the main thread (one sqlite3 connection
  isn't safe to share across threads). Was one serial round-trip at a time.
- ✅ **Hardware-aware workers** — minimal `embed_workers()` inline in `embed_repos.py`:
  GPU present (`nvidia-smi`/`xpu-smi` on PATH) → 6, else 3. Matches DocuBrowser's
  `recommended_embed_workers`.

**Deliberately NOT ported** (ponytail — they don't fit repo-browser):
- `recommended_scan_workers` / ProcessPool scan — that's for RAM-heavy pdfplumber
  extraction. repo-browser's scan is light git-subprocess + README read with serial DB
  writes; parallelizing buys little and adds complexity. Scan stays serial.
- **psutil dependency** — repo-browser's README promises "stdlib only, no pip installs".
  The GPU check is stdlib (`shutil.which`); we did not add psutil.
- `wait_for_memory` / memory-pressure backoff — guards the PDF pool's multi-GB workers.
  Embedding is light (text → vector); no memory guard needed.
- Full `hardware_utils.py` module + hardware summary banner — one consumer (embed), so
  the ~10-line heuristic is inlined rather than made a module. Extract if Tier 4 needs it.

## Tier 4 — capability, more work (not started)

- ⬜ **Deep Links** (`deep_links.py`) — jump to the passage inside a doc. For repos =
  jump to README location. Low value (READMEs short), high port cost.
- ⬜ **LLM synopsis** (dolphin3) — DocuBrowser generates AI summary. repo-browser has
  `summary` = first README paragraph, heuristic-extracted (`scan_repos.py`
  `extract_summary`). Cheaper, arguably fine.
- ⬜ **Cross-platform paths** (`platform_paths.py`) — XDG / macOS / Windows. repo-browser
  is `/etc/rb.config` + workDir, WSL-only for Windows.
- ⬜ **Packaging** — DocuBrowser ships rpm/deb/tar/win/mac. repo-browser: tarball only.
