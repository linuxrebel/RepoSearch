#!/usr/bin/env python3
"""
Repo Browser — local search server.
FTS5 keyword search + cosine similarity semantic search, merged and ranked.
Run: python3 repo_search.py
"""

import os
import json
import math
import struct
import sqlite3
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from rb_config import get_db_path, get_git_root, get_work_dir, get_config_source, load_config

DB_PATH = get_db_path()
OLLAMA_URL = 'http://localhost:11434/api/embed'
EMBED_MODEL = 'nomic-embed-text'
PORT = 8642


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


def fts_search(conn, query, limit=50):
    """FTS5 keyword search. Returns list of (repo_id, rank)."""
    clean = query.replace('"', '').replace("'", '')
    # Try exact phrase first, then individual tokens
    results = []
    try:
        rows = conn.execute('''
            SELECT rowid, rank FROM repo_fts
            WHERE repo_fts MATCH ?
            ORDER BY rank LIMIT ?
        ''', (f'"{clean}"', limit)).fetchall()
        results = [(r[0], r[1]) for r in rows]
    except Exception:
        pass
    # Also try token match (OR) for partial coverage
    tokens = clean.split()
    if len(tokens) > 1 or not results:
        try:
            token_query = ' OR '.join(t for t in tokens if t)
            rows = conn.execute('''
                SELECT rowid, rank FROM repo_fts
                WHERE repo_fts MATCH ?
                ORDER BY rank LIMIT ?
            ''', (token_query, limit)).fetchall()
            existing = {r[0] for r in results}
            for r in rows:
                if r[0] not in existing:
                    results.append((r[0], r[1]))
        except Exception:
            pass
    return results


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


def name_match_score(query, repo_name):
    """Boost for query appearing in repo name."""
    ql = query.lower()
    nl = repo_name.lower()
    if ql == nl:
        return 1.0     # Exact match
    if ql in nl or nl in ql:
        return 0.7      # Substring match (MonVisor in MonVisor-Corpus)
    # Token overlap
    qt = set(ql.replace('-', ' ').replace('_', ' ').split())
    nt = set(nl.replace('-', ' ').replace('_', ' ').split())
    overlap = qt & nt
    if overlap:
        return 0.4 * len(overlap) / max(len(qt), 1)
    return 0.0


def merged_search(conn, query, limit=30):
    """Combine FTS5, semantic, and name-match into a single ranked list."""
    # FTS search — normalize to 0..1 range
    fts_results = fts_search(conn, query)
    fts_scores = {}
    if fts_results:
        # FTS ranks are negative; more negative = better
        best = abs(min(r[1] for r in fts_results))
        for repo_id, rank in fts_results:
            fts_scores[repo_id] = abs(rank) / best if best > 0 else 0

    # Semantic search — already 0..1, with floor applied
    sem_scores = {}
    try:
        query_vec = get_embedding(query)
        sem_results = semantic_search(conn, query_vec, threshold=0.30)
        sem_scores = {repo_id: sim for repo_id, sim in sem_results}
    except Exception:
        pass

    # Name-match boost — fetch names for all candidates
    all_ids = set(fts_scores.keys()) | set(sem_scores.keys())
    name_scores = {}
    if all_ids:
        placeholders = ','.join('?' * len(all_ids))
        id_list = list(all_ids)
        rows = conn.execute(
            f'SELECT id, name FROM repos WHERE id IN ({placeholders})', id_list
        ).fetchall()
        for r in rows:
            name_scores[r[0]] = name_match_score(query, r[1])

    # Also check ALL repo names for direct name hits (catches cases FTS/semantic missed)
    all_names = conn.execute('SELECT id, name FROM repos').fetchall()
    for r in all_names:
        ns = name_match_score(query, r[1])
        if ns > 0 and r[0] not in all_ids:
            all_ids.add(r[0])
            name_scores[r[0]] = ns

    # Tag-match boost — repos with a matching tag score higher
    query_lower = query.lower().strip()
    query_tokens = set(query_lower.replace('-', ' ').replace('_', ' ').split())
    tag_scores = {}
    if all_ids:
        placeholders = ','.join('?' * len(list(all_ids)))
        id_list = list(all_ids)
        tag_rows = conn.execute(
            f'SELECT repo_id, tag FROM repo_tags WHERE repo_id IN ({placeholders})',
            id_list
        ).fetchall()
        for repo_id, tag in tag_rows:
            if tag == query_lower or tag in query_tokens:
                tag_scores[repo_id] = 1.0
            elif query_lower in tag or tag in query_lower:
                tag_scores.setdefault(repo_id, 0.5)

    # Also find repos with matching tags that weren't in FTS/semantic results
    tag_match_rows = conn.execute(
        'SELECT DISTINCT repo_id FROM repo_tags WHERE tag = ?', (query_lower,)
    ).fetchall()
    for r in tag_match_rows:
        if r[0] not in all_ids:
            all_ids.add(r[0])
            tag_scores[r[0]] = 1.0

    # Merge: weighted blend with name + tag boost
    merged = []
    for rid in all_ids:
        fts_s = fts_scores.get(rid, 0.0)
        sem_s = sem_scores.get(rid, 0.0)
        name_s = name_scores.get(rid, 0.0)
        tag_s = tag_scores.get(rid, 0.0)
        # Weight: 0.2 keyword + 0.3 semantic + 0.2 name match + 0.3 tag match
        combined = 0.2 * fts_s + 0.3 * sem_s + 0.2 * name_s + 0.3 * tag_s
        # Filter noise: need at least one strong signal
        has_keyword = fts_s > 0
        has_name = name_s > 0
        has_tag = tag_s > 0
        if not has_keyword and not has_name and not has_tag:
            if sem_s < 0.65:
                continue
        elif combined < 0.10:
            continue
        merged.append((rid, combined, fts_s, sem_s))

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
    def do_GET(self):
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
            with open(html_path, 'rb') as f:
                self.wfile.write(f.read())
        else:
            super().do_GET()

    def do_POST(self):
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
