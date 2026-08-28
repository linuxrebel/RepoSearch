#!/usr/bin/env python3
# repo-browser -- HTTP search server with FTS5 and semantic search
# Copyright (C) 2026 James Sparenberg
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
Repo Browser — local search server.
FTS5 keyword search + cosine similarity semantic search, merged and ranked.
Run: python3 repo_search.py
"""

import os
import re
import json
import math
import secrets
import struct
import shutil
import difflib
import sqlite3
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from rb_config import get_db_path, get_git_root, get_work_dir, get_config_source, load_config

# Articles + coordinating conjunctions (FANBOYS). Stripped from a query before
# it is embedded for semantic search: they carry little meaning and dilute the
# query vector (e.g. "freedom and liberty" embeds better without "and").
_STOPWORDS = frozenset({
    'a', 'an', 'the',
    'and', 'or', 'but', 'nor', 'for', 'so', 'yet',
})


def strip_stopwords(query):
    """Drop articles/conjunctions from *query* for semantic embedding.

    Keyword/FTS search keeps the original query; this only shapes the vector
    fed to the embedder. Returns the original query unchanged if filtering
    would empty it (an all-stopword query still embeds to something).
    """
    kept = [w for w in query.split()
            if re.sub(r'[^a-z]', '', w.lower()) not in _STOPWORDS]
    return ' '.join(kept) if kept else query

DB_PATH = get_db_path()
OLLAMA_URL = 'http://localhost:11434/api/embed'
OLLAMA_GEN_URL = 'http://localhost:11434/api/generate'
EMBED_MODEL = 'nomic-embed-text'
SYNOPSIS_MODEL = 'dolphin3:latest'
SYNOPSIS_TIMEOUT = 120   # dolphin3 can be slow on first load / CPU inference
PORT = 8642

# Per-process CSRF secret, injected into served HTML and required on POST.
CSRF_TOKEN = secrets.token_urlsafe(32)


def _hostname_allowed(hostname):
    """True for loopback host names only — the server is loopback-bound."""
    hostname = (hostname or '').strip('[]').lower()
    return (hostname in ('', 'localhost')
            or hostname == '::1'
            or hostname.startswith('127.'))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure tables exist (handles fresh install with no DB)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, url TEXT,
            description TEXT, readme_snippet TEXT, last_commit TEXT,
            default_branch TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS repo_tags (
            repo_id INTEGER NOT NULL, tag TEXT NOT NULL, source TEXT DEFAULT 'auto',
            FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            UNIQUE(repo_id, tag)
        );
        CREATE TABLE IF NOT EXISTS repo_embeddings (
            repo_id INTEGER PRIMARY KEY, embedding BLOB, model TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(
            name, description, readme_snippet, tags, content='', content_rowid='rowid'
        );
    ''')
    # Migrate: cached on-demand synopsis + its provenance (readme|online|code).
    cols = [r[1] for r in conn.execute('PRAGMA table_info(repos)').fetchall()]
    for col in ('synopsis', 'synopsis_kind'):
        if col not in cols:
            conn.execute(f'ALTER TABLE repos ADD COLUMN {col} TEXT')
    return conn


def get_embedding(text):
    payload = json.dumps({'model': EMBED_MODEL, 'input': text}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data['embeddings'][0]


def blob_to_vec(blob):
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_fts_expr(query):
    """Build a safe FTS5 MATCH expression.

    Quoted phrases ("import fmt") pass through as phrase literals; bare words
    become a prefix-OR ("import"* OR "fmt"*) so partial words match. Each token
    is FTS5-quoted so user input can't inject FTS5 operators (AND/OR/NEAR/:).
    """
    parts = []
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]*"', ' ', query)
    bare_tokens = re.findall(r'\w+', remainder.lower())
    for phrase in phrases:
        inner = phrase.strip().lower()
        if inner:
            parts.append(f'"{inner}"')
    for t in bare_tokens:
        parts.append(f'"{t}"*')
    return ' OR '.join(parts) if parts else None


def fts_search(conn, query):
    """BM25 keyword search over repo_fts. Returns {repo_id: score 0..1}.

    Column weights (name, description, readme_snippet, tags) boost name and
    tag hits over README body — this subsumes the old separate name/tag
    boosts. bm25 returns more-negative for better matches; flip + normalize.
    """
    expr = build_fts_expr(query)
    if not expr:
        return {}
    try:
        rows = conn.execute(
            'SELECT rowid, bm25(repo_fts, 8.0, 3.0, 3.0, 5.0) AS rank '
            'FROM repo_fts WHERE repo_fts MATCH ? ORDER BY rank',
            (expr,)
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    raw = {rid: -rank for rid, rank in rows}
    best = max(raw.values())
    if best <= 0:
        return {}
    return {rid: val / best for rid, val in raw.items()}


def semantic_search(conn, query_vec, threshold=0.3, limit=50):
    """Cosine similarity search. Only returns results above threshold."""
    rows = conn.execute(
        'SELECT repo_id, embedding FROM repo_embeddings'
    ).fetchall()
    scored = []
    for row in rows:
        vec = blob_to_vec(row['embedding'])
        sim = cosine_sim(query_vec, vec)
        if sim >= threshold:
            scored.append((row['repo_id'], sim))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def merged_search(conn, query, limit=30, include_hidden=False):
    """Hybrid BM25 keyword + semantic, ranked by max(fts,sem)+0.1·min.

    Two signals: bm25 keyword score (name/tag columns weighted so those hits
    win) and cosine semantic score. A repo needs either a keyword hit or a
    semantic score above the noise floor to appear; max() keeps a strong
    keyword match from being diluted by a zero semantic score and vice versa,
    with a small boost when both fire. Returns (repo_id, final, fts, sem).
    """
    kw_scores = fts_search(conn, query)

    sem_scores = {}
    try:
        query_vec = get_embedding(strip_stopwords(query))
        sem_scores = dict(semantic_search(conn, query_vec, threshold=0.30))
    except Exception:
        pass

    sem_floor = 0.30
    hidden = set() if include_hidden else hidden_ids(conn)
    all_ids = (set(kw_scores) | {rid for rid, s in sem_scores.items() if s >= sem_floor}) - hidden
    merged = []
    for rid in all_ids:
        fts_s = kw_scores.get(rid, 0.0)
        sem_s = sem_scores.get(rid, 0.0)
        if fts_s > 0 and sem_s >= sem_floor:
            final = min(max(fts_s, sem_s) + 0.1 * min(fts_s, sem_s), 1.0)
        else:
            final = max(fts_s, sem_s)
        if final > 0.01:
            merged.append((rid, final, fts_s, sem_s))

    merged.sort(key=lambda x: -x[1])
    return merged[:limit]


def read_repo_readme(conn, repo_path):
    """Read a README for an indexed repo. Returns {ok, name, raw}.

    Only serves READMEs of repos already in the DB — this validates the
    client-supplied path against the index, so it can't read arbitrary files.
    """
    row = conn.execute('SELECT path FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if not row:
        return {'ok': False, 'error': 'Unknown repo'}
    for name in ('README.md', 'README.rst', 'README.txt', 'README'):
        fp = os.path.join(repo_path, name)
        if os.path.isfile(fp):
            try:
                with open(fp, encoding='utf-8', errors='ignore') as f:
                    raw = f.read()
            except OSError as ex:
                return {'ok': False, 'error': str(ex)}
            return {'ok': True, 'name': os.path.basename(repo_path), 'raw': raw}
    return {'ok': False, 'error': 'No README found'}


# ── Synopsis generation ───────────────────────────────────────────────────────
# On-demand chain, cached in the DB: (1) the developer's own description from the
# README — verbatim, no rephrasing; (2) the repo host's description field, still
# verbatim; (3) only if nothing is authored anywhere, an AI summary read from the
# bounded code. Steps 1-2 are the author's words; step 3 is clearly labelled.

_DESC_HEADINGS = ('description', 'overview', 'about', 'introduction', 'what is')


def extract_heading_description(raw):
    """Return the verbatim body under an explicit description-type heading
    (## Description / Overview / About / Introduction / What is …), or None."""
    lines = raw.split('\n')
    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if not m:
            continue
        heading = m.group(2).strip().lower().rstrip(':')
        if any(heading == h or heading.startswith(h + ' ') or heading.startswith(h)
               for h in _DESC_HEADINGS):
            level = len(m.group(1))
            body = []
            for nxt in lines[i + 1:]:
                hm = re.match(r'^(#{1,6})\s', nxt)
                if hm and len(hm.group(1)) <= level:
                    break
                body.append(nxt)
            text = '\n'.join(body).strip()
            if text:
                return text
    return None


def ollama_generate(prompt):
    """Single non-streaming /api/generate call. Returns text or None."""
    payload = json.dumps({'model': SYNOPSIS_MODEL, 'prompt': prompt, 'stream': False}).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_GEN_URL, data=payload, headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=SYNOPSIS_TIMEOUT) as resp:
            data = json.loads(resp.read())
        return (data.get('response') or '').strip() or None
    except Exception:
        return None


def _collapse_ws(s):
    return re.sub(r'\s+', ' ', s).strip().lower()


def _verbatim_enough(reply, raw, threshold=0.85):
    """True if *reply* is essentially the developer's words lifted from *raw*.

    80/20, not paranoid: fraction of the reply that aligns character-for-
    character with the README. A fixed typo or an added article barely moves
    it; a genuine paraphrase (new words, invented content) tanks it. Whitespace
    is normalized first — reflowing wrapping is not a word change.
    """
    a, b = _collapse_ws(reply), _collapse_ws(raw)
    if not a:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    return matched / len(a) >= threshold


def ai_extract_description(raw):
    """Ask the model to copy the README's description passage, or NONE.

    Accepted only if the reply is essentially the README's own words
    (`_verbatim_enough`) — trivial fixes are fine, a paraphrase is not.
    """
    prompt = (
        "Below is a project's README. If it contains a written description of "
        "what the project is, copy that description passage as written — do not "
        "summarize, reword, or add content of your own. If there is no such "
        "description, reply with exactly: NONE. Output only the copied text or "
        "NONE, nothing else.\n\nREADME:\n" + raw[:6000]
    )
    reply = ollama_generate(prompt)
    if not reply or reply.strip().upper() == 'NONE':
        return None
    if _verbatim_enough(reply, raw):
        return reply.strip()
    return None   # genuinely paraphrased — reject, fall through


def fetch_remote_description(url):
    """Best-effort fetch of a GitHub/GitLab repo description (verbatim, dev's
    own field). Returns the description string or None. Silent on any failure —
    offline, no remote, rate-limited, or empty all fall through."""
    if not url:
        return None
    m = re.search(r'(github\.com|gitlab\.com)[/:]+([^/]+)/(.+?)(?:\.git)?/?$', url.strip())
    if not m:
        return None
    host, owner, repo = m.group(1), m.group(2), m.group(3)
    if host == 'github.com':
        api = f'https://api.github.com/repos/{owner}/{repo}'
    else:
        api = f'https://gitlab.com/api/v4/projects/{quote(owner + "/" + repo, safe="")}'
    try:
        req = urllib.request.Request(api, headers={'User-Agent': 'repo-browser'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return (data.get('description') or '').strip() or None
    except Exception:
        return None


_MANIFESTS = ('package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'pom.xml',
              'requirements.txt', 'setup.py', 'Gemfile', 'composer.json', 'build.gradle')
_CODE_EXT = ('.py', '.js', '.ts', '.go', '.rs', '.rb', '.java', '.c', '.cpp',
             '.sh', '.php', '.swift', '.kt')
_SKIP_DIRS = {'.git', 'node_modules', 'vendor', '__pycache__', '.venv', 'venv', 'dist', 'build'}


def _read_head(fp, n):
    try:
        with open(fp, encoding='utf-8', errors='ignore') as f:
            return ''.join(f.readlines()[:n])
    except OSError:
        return ''


def gather_code_bundle(repo_path, max_files=8, head_lines=60):
    """Bounded snapshot of a repo for the AI fallback: file tree + manifests +
    the head of a few main source files. Small repos (the only ones that reach
    this path — large repos have docs) fit comfortably."""
    tree = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        if root[len(repo_path):].count(os.sep) > 2:
            dirs[:] = []
            continue
        for f in sorted(files):
            tree.append(os.path.relpath(os.path.join(root, f), repo_path))
    parts = ['Files:\n' + '\n'.join(tree[:200])]
    for name in _MANIFESTS:
        fp = os.path.join(repo_path, name)
        if os.path.isfile(fp):
            parts.append(f'--- {name} ---\n' + _read_head(fp, 80))
    picked = 0
    for rel in tree:
        if picked >= max_files:
            break
        if rel.endswith(_CODE_EXT):
            parts.append(f'--- {rel} ---\n' + _read_head(os.path.join(repo_path, rel), head_lines))
            picked += 1
    return '\n\n'.join(parts)


def synopsis_from_readme_or_online(conn, repo_path):
    """Rungs 1-2: the developer's own words. Returns (text, kind) or (None, None).
    kind is 'readme' or 'online'."""
    row = conn.execute('SELECT url FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if row is None:
        return None, None
    readme = read_repo_readme(conn, repo_path)
    raw = readme.get('raw', '') if readme.get('ok') else ''
    if raw:
        desc = extract_heading_description(raw)
        if desc:
            return desc, 'readme'
        ai = ai_extract_description(raw)
        if ai:
            return ai, 'readme'
    remote = fetch_remote_description(row['url'])
    if remote:
        return remote, 'online'
    return None, None


def synopsis_from_code(conn, repo_path):
    """Rung 3: AI summary read from the bounded code. Returns (text, 'code') or
    (None, None)."""
    row = conn.execute('SELECT name FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if row is None:
        return None, None
    bundle = gather_code_bundle(repo_path)
    prompt = (
        f"You are reading the source of a code project named '{row['name']}'. "
        "Based only on the files below, write a concise 2-4 sentence description "
        "of what this project is and does. Describe only what the code shows; do "
        "not invent features. No markdown, no preamble — output only the "
        "description.\n\n" + bundle[:12000]
    )
    text = ollama_generate(prompt)
    return (text, 'code') if text else (None, None)


def get_repo_details(conn, repo_ids):
    """Fetch full repo info for a list of IDs."""
    if not repo_ids:
        return {}
    placeholders = ','.join('?' * len(repo_ids))
    rows = conn.execute(f'''
        SELECT r.*, GROUP_CONCAT(t.tag, ', ') as tags
        FROM repos r
        LEFT JOIN repo_tags t ON r.id = t.repo_id
        WHERE r.id IN ({placeholders})
        GROUP BY r.id
    ''', repo_ids).fetchall()
    return {r['id']: dict(r) for r in rows}


def hidden_ids(conn):
    """Repo ids the user has hidden (tagged 'hidden')."""
    return {r[0] for r in conn.execute("SELECT repo_id FROM repo_tags WHERE tag = 'hidden'")}


def get_all_repos(conn, include_hidden=False):
    """Return all repos with tags. Hidden repos excluded unless include_hidden."""
    where = '' if include_hidden else \
        "WHERE r.id NOT IN (SELECT repo_id FROM repo_tags WHERE tag = 'hidden')"
    rows = conn.execute(f'''
        SELECT r.*, GROUP_CONCAT(t.tag, ', ') as tags
        FROM repos r LEFT JOIN repo_tags t ON r.id = t.repo_id
        {where}
        GROUP BY r.id ORDER BY r.name
    ''').fetchall()
    return [dict(r) for r in rows]


def get_all_tags(conn):
    """Return all unique tags with counts (the 'hidden' marker excluded)."""
    rows = conn.execute('''
        SELECT tag, count(*) as cnt FROM repo_tags
        WHERE tag != 'hidden'
        GROUP BY tag ORDER BY cnt DESC
    ''').fetchall()
    return [{'tag': r[0], 'count': r[1]} for r in rows]


def refresh_repo_fts(conn, repo_id):
    """Rewrite one repo's FTS row so manual tag changes are searchable."""
    r = conn.execute('SELECT name, description, readme_snippet FROM repos WHERE id = ?',
                     (repo_id,)).fetchone()
    if not r:
        return
    tags = conn.execute('SELECT GROUP_CONCAT(tag, " ") FROM repo_tags WHERE repo_id = ?',
                        (repo_id,)).fetchone()[0] or ''
    conn.execute(
        'INSERT OR REPLACE INTO repo_fts(rowid, name, description, readme_snippet, tags) '
        'VALUES (?, ?, ?, ?, ?)',
        (repo_id, r['name'], r['description'] or '', r['readme_snippet'] or '', tags))


def add_repo_tags(conn, repo_path, tags_csv):
    """Add manual tags to a repo. Returns (ok, added_list)."""
    row = conn.execute('SELECT id FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if not row:
        return False, []
    rid = row['id']
    added = []
    for tag in tags_csv.split(','):
        tag = tag.strip().lower()
        if tag:
            conn.execute("INSERT OR IGNORE INTO repo_tags(repo_id, tag, source) VALUES (?, ?, 'manual')",
                         (rid, tag))
            added.append(tag)
    refresh_repo_fts(conn, rid)
    conn.commit()
    return True, added


def remove_repo_tag(conn, repo_path, tag):
    """Remove a tag from a repo (used to unhide: tag='hidden'). Returns ok."""
    row = conn.execute('SELECT id FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if not row:
        return False
    conn.execute('DELETE FROM repo_tags WHERE repo_id = ? AND tag = ?',
                 (row['id'], tag.strip().lower()))
    refresh_repo_fts(conn, row['id'])
    conn.commit()
    return True


def delete_repo(conn, repo_path):
    """Nuclear: rm -rf the repo clone from disk and purge it from the index.
    Refuses any path that isn't an indexed repo living under gitParent."""
    row = conn.execute('SELECT id, path FROM repos WHERE path = ?', (repo_path,)).fetchone()
    if not row:
        return False, 'Unknown repo'
    real = os.path.realpath(row['path'])
    git_root = os.path.realpath(get_git_root())
    if not (real == git_root or real.startswith(git_root + os.sep)):
        return False, 'Refusing to delete outside the git root'
    try:
        shutil.rmtree(real)
    except OSError as ex:
        return False, str(ex)
    rid = row['id']
    old = conn.execute('SELECT name, description, readme_snippet FROM repos WHERE id = ?',
                       (rid,)).fetchone()
    old_tags = conn.execute('SELECT GROUP_CONCAT(tag, " ") FROM repo_tags WHERE repo_id = ?',
                            (rid,)).fetchone()[0] or ''
    conn.execute(
        "INSERT INTO repo_fts(repo_fts, rowid, name, description, readme_snippet, tags) "
        "VALUES('delete', ?, ?, ?, ?, ?)",
        (rid, old['name'], old['description'] or '', old['readme_snippet'] or '', old_tags))
    conn.execute('DELETE FROM repo_tags WHERE repo_id = ?', (rid,))
    conn.execute('DELETE FROM repo_embeddings WHERE repo_id = ?', (rid,))
    conn.execute('DELETE FROM repos WHERE id = ?', (rid,))
    conn.commit()
    return True, None


class RepoHandler(SimpleHTTPRequestHandler):
    def _host_allowed(self):
        """Reject requests whose Host header isn't a loopback name.

        The server binds 127.0.0.1, but a browser tricked by DNS rebinding
        (attacker.com re-resolved to 127.0.0.1) would still send a foreign
        Host header. Allow only loopback host names; a present port must match.
        """
        host = self.headers.get('Host', '')
        if not host:
            return True  # HTTP/1.0 clients may omit Host
        hostname, _, port = host.rpartition(':')
        if not hostname:            # no colon → all of it landed in `port`
            hostname, port = port, ''
        if port and port != str(PORT):
            return False
        return _hostname_allowed(hostname)

    def _guard_mutation(self):
        """Require the per-process CSRF token on state-changing requests.

        The token is injected into the same-origin HTML; a cross-origin
        attacker page cannot read it, so it cannot forge the X-CSRF-Token
        header. Sends 403 and returns False on failure.
        """
        token = self.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(token, CSRF_TOKEN):
            self.json_response({'error': 'Forbidden: missing or invalid CSRF token'}, 403)
            return False
        return True

    def do_GET(self):
        if not self._host_allowed():
            self.json_response({'error': 'Forbidden: invalid Host header'}, 403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/api/search':
            q = params.get('q', [''])[0]
            inc = params.get('hidden', ['0'])[0] == '1'
            conn = get_db()
            hidden_count = len(hidden_ids(conn))
            if not q:
                repos = get_all_repos(conn, include_hidden=inc)
                self.json_response({'repos': repos, 'total': len(repos), 'hidden_count': hidden_count})
            else:
                results = merged_search(conn, q, include_hidden=inc)
                repo_ids = [r[0] for r in results]
                details = get_repo_details(conn, repo_ids)
                repos = []
                for rid, combined, fts_s, sem_s in results:
                    if rid in details:
                        repo = details[rid]
                        repo['score'] = round(combined, 4)
                        repo['fts_score'] = round(fts_s, 4)
                        repo['sem_score'] = round(sem_s, 4)
                        repos.append(repo)
                self.json_response({'repos': repos, 'total': len(repos), 'query': q,
                                    'hidden_count': hidden_count})
            conn.close()

        elif path == '/api/tags':
            conn = get_db()
            tags = get_all_tags(conn)
            self.json_response({'tags': tags})
            conn.close()

        elif path == '/api/stats':
            conn = get_db()
            total = conn.execute('SELECT count(*) FROM repos').fetchone()[0]
            embedded = conn.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
            tag_count = conn.execute('SELECT count(DISTINCT tag) FROM repo_tags').fetchone()[0]
            self.json_response({
                'total_repos': total,
                'embedded': embedded,
                'unique_tags': tag_count
            })
            conn.close()

        elif path == '/api/config':
            cfg = load_config()
            source = get_config_source()
            self.json_response({
                'gitParent': cfg.get('gitParent', ''),
                'workDir': cfg.get('workDir', ''),
                'configSource': source,
                'installed': source == '/etc/rb.config'
            })

        elif path == '/api/readme':
            repo_path = params.get('path', [''])[0]
            conn = get_db()
            self.json_response(read_repo_readme(conn, repo_path))
            conn.close()

        elif path == '/api/synopsis':
            # Cached synopsis only. Generation is the POST below (CSRF-guarded).
            repo_path = params.get('path', [''])[0]
            conn = get_db()
            row = conn.execute(
                'SELECT synopsis, synopsis_kind FROM repos WHERE path = ?', (repo_path,)
            ).fetchone()
            conn.close()
            if row and row['synopsis']:
                self.json_response({'ok': True, 'cached': True,
                                    'synopsis': row['synopsis'], 'kind': row['synopsis_kind']})
            else:
                self.json_response({'ok': True, 'cached': False})

        elif path == '/api/browse':
            # List directories for the path browser
            browse_path = params.get('path', ['/'])[0]
            try:
                entries = []
                if browse_path != '/':
                    entries.append({'name': '..', 'path': os.path.dirname(browse_path), 'isDir': True})
                for e in sorted(os.listdir(browse_path)):
                    full = os.path.join(browse_path, e)
                    if os.path.isdir(full) and not e.startswith('.'):
                        entries.append({'name': e, 'path': full, 'isDir': True})
                self.json_response({'path': browse_path, 'entries': entries})
            except PermissionError:
                self.json_response({'path': browse_path, 'entries': [], 'error': 'Permission denied'})
            except FileNotFoundError:
                self.json_response({'path': browse_path, 'entries': [], 'error': 'Not found'})

        elif path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            with open(html_path, 'r', encoding='utf-8') as f:
                html = f.read()
            # Inject the per-process CSRF token so same-origin JS can read it.
            meta = f'<meta name="csrf-token" content="{CSRF_TOKEN}">'
            html = html.replace('</head>', meta + '\n</head>', 1)
            self.wfile.write(html.encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        if not self._host_allowed():
            self.json_response({'error': 'Forbidden: invalid Host header'}, 403)
            return
        if not self._guard_mutation():
            return
        parsed = urlparse(self.path)
        if parsed.path == '/api/synopsis':
            # Two-stage so the UI can show the code-reading notice only when we
            # actually fall through to it: stage=light does README + host; the
            # client calls stage=code only if light found nothing.
            q = parse_qs(parsed.query)
            repo_path = q.get('path', [''])[0]
            stage = q.get('stage', ['light'])[0]
            conn = get_db()
            if stage == 'code':
                text, kind = synopsis_from_code(conn, repo_path)
            else:
                text, kind = synopsis_from_readme_or_online(conn, repo_path)
            if text:
                conn.execute('UPDATE repos SET synopsis = ?, synopsis_kind = ? WHERE path = ?',
                             (text, kind, repo_path))
                conn.commit()
            conn.close()
            if text:
                self.json_response({'ok': True, 'found': True, 'synopsis': text, 'kind': kind})
            else:
                self.json_response({'ok': True, 'found': False})
            return

        if parsed.path == '/api/add-tags':
            # Add manual tags (Hide uses this with tags=hidden).
            q = parse_qs(parsed.query)
            conn = get_db()
            ok, added = add_repo_tags(conn, q.get('path', [''])[0], q.get('tags', [''])[0])
            conn.close()
            self.json_response({'ok': ok, 'added': added} if ok
                               else {'ok': False, 'error': 'Unknown repo'},
                               200 if ok else 404)
            return

        if parsed.path == '/api/remove-tag':
            # Remove a tag (unhide uses this with tag=hidden).
            q = parse_qs(parsed.query)
            conn = get_db()
            ok = remove_repo_tag(conn, q.get('path', [''])[0], q.get('tag', [''])[0])
            conn.close()
            self.json_response({'ok': ok} if ok else {'ok': False, 'error': 'Unknown repo'},
                               200 if ok else 404)
            return

        if parsed.path == '/api/delete':
            # Nuclear delete: rm -rf the clone + purge from index.
            q = parse_qs(parsed.query)
            conn = get_db()
            ok, err = delete_repo(conn, q.get('path', [''])[0])
            conn.close()
            self.json_response({'ok': True} if ok else {'ok': False, 'error': err},
                               200 if ok else 400)
            return

        if parsed.path == '/api/config':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            git_parent = body.get('gitParent', '').strip()
            work_dir = body.get('workDir', '').strip()
            if not git_parent or not work_dir:
                self.json_response({'error': 'Both fields required'}, 400)
                return
            # Write config to workDir (user must copy to /etc)
            config_content = f"""# repo-browser configuration
# Move this file to /etc/rb.config

# Parent directory containing all git repos
gitParent={git_parent}

# Working directory where repo-browser files live
workDir={work_dir}
"""
            out_path = os.path.join(get_work_dir(), 'rb.config')
            with open(out_path, 'w') as f:
                f.write(config_content)
            self.json_response({
                'saved': out_path,
                'message': f'Config saved to {out_path}. Copy to /etc/rb.config:\n  sudo cp {out_path} /etc/rb.config'
            })
        else:
            self.send_response(404)
            self.end_headers()

    def json_response(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Quieter logging
        pass


if __name__ == '__main__':
    cfg_src = get_config_source()
    print(f"Repo Browser listening on http://localhost:{PORT}")
    print(f"  Config: {cfg_src or 'none (configure via gear icon)'}")
    print(f"  DB: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print(f"  First run — open the UI and click the gear icon to configure")
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(('127.0.0.1', PORT), RepoHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
