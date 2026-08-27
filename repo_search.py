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
import sqlite3
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
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
EMBED_MODEL = 'nomic-embed-text'
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


def merged_search(conn, query, limit=30):
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
    all_ids = set(kw_scores) | {rid for rid, s in sem_scores.items() if s >= sem_floor}
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


def get_all_repos(conn):
    """Return all repos with tags."""
    rows = conn.execute('''
        SELECT r.*, GROUP_CONCAT(t.tag, ', ') as tags
        FROM repos r LEFT JOIN repo_tags t ON r.id = t.repo_id
        GROUP BY r.id ORDER BY r.name
    ''').fetchall()
    return [dict(r) for r in rows]


def get_all_tags(conn):
    """Return all unique tags with counts."""
    rows = conn.execute('''
        SELECT tag, count(*) as cnt FROM repo_tags
        GROUP BY tag ORDER BY cnt DESC
    ''').fetchall()
    return [{'tag': r[0], 'count': r[1]} for r in rows]


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
            conn = get_db()
            if not q:
                repos = get_all_repos(conn)
                self.json_response({'repos': repos, 'total': len(repos)})
            else:
                results = merged_search(conn, q)
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
                self.json_response({'repos': repos, 'total': len(repos), 'query': q})
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
