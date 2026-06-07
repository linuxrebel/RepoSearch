#!/usr/bin/env python3
"""
Repo scanner — walks /home/james/git, extracts metadata, generates tags,
populates SQLite DB. Idempotent: safe to rerun.
"""

import os
import re
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
from collections import Counter
from rb_config import get_git_root, get_db_path

GIT_ROOT = get_git_root()
DB_PATH = get_db_path()

# Language detection by file extension
EXT_LANG = {
    '.py': 'python', '.go': 'golang', '.rs': 'rust', '.js': 'javascript',
    '.ts': 'typescript', '.jsx': 'react', '.tsx': 'react', '.rb': 'ruby',
    '.java': 'java', '.kt': 'kotlin', '.cs': 'csharp', '.cpp': 'cpp',
    '.c': 'c', '.h': 'c', '.sh': 'bash', '.zsh': 'zsh',
    '.tf': 'terraform', '.hcl': 'terraform', '.yaml': 'yaml', '.yml': 'yaml',
    '.json': 'json', '.toml': 'toml', '.lua': 'lua', '.pl': 'perl',
    '.r': 'r', '.R': 'r', '.swift': 'swift', '.dart': 'dart',
    '.html': 'html', '.css': 'css', '.scss': 'scss',
    '.sql': 'sql', '.graphql': 'graphql', '.proto': 'protobuf',
    '.dockerfile': 'docker', '.ex': 'elixir', '.erl': 'erlang',
    '.zig': 'zig', '.nim': 'nim', '.jl': 'julia', '.ps1': 'powershell',
}

# Common infra/tool keywords to detect in README
KEYWORD_TAGS = [
    'kubernetes', 'k8s', 'docker', 'container', 'helm', 'ansible',
    'terraform', 'puppet', 'chef', 'prometheus', 'grafana', 'alertmanager',
    'monitoring', 'observability', 'telemetry', 'opentelemetry', 'otel',
    'nginx', 'apache', 'caddy', 'traefik',
    'aws', 'gcp', 'azure', 'cloud',
    'ci/cd', 'cicd', 'jenkins', 'github-actions', 'gitlab-ci', 'argo',
    'security', 'compliance', 'vulnerability', 'scanning', 'sast', 'dast',
    'machine-learning', 'ml', 'ai', 'llm', 'neural', 'deep-learning',
    'api', 'rest', 'grpc', 'microservice',
    'database', 'postgres', 'mysql', 'redis', 'mongodb', 'elasticsearch',
    'linux', 'ebpf', 'networking', 'dns', 'tls', 'ssl',
    'automation', 'pipeline', 'workflow', 'orchestration',
    'logging', 'log', 'siem', 'splunk', 'datadog',
    'backup', 'disaster-recovery', 'ha', 'high-availability',
    'git', 'version-control', 'devops', 'sre', 'platform',
    'testing', 'test', 'benchmark', 'load-test',
    'cli', 'tool', 'utility', 'sdk', 'library', 'framework',
    'web', 'frontend', 'backend', 'fullstack',
    'ollama', 'langchain', 'rag', 'embedding', 'fine-tuning', 'finetune',
    'calico', 'tigera', 'istio', 'envoy', 'service-mesh',
    'wsl', 'windows',
]


def init_db(conn):
    """Create tables if they don't exist."""
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            url TEXT,
            description TEXT,
            summary TEXT,
            readme_snippet TEXT,
            last_commit TEXT,
            default_branch TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        -- migrate: add summary column if upgrading from older schema
        -- (SQLite ignores this if column already exists via the CREATE above,
        --  but existing DBs need an explicit ALTER)


        CREATE TABLE IF NOT EXISTS repo_tags (
            repo_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            source TEXT DEFAULT 'auto',
            FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            UNIQUE(repo_id, tag)
        );

        CREATE TABLE IF NOT EXISTS repo_embeddings (
            repo_id INTEGER PRIMARY KEY,
            embedding BLOB,
            model TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT DEFAULT (datetime('now')),
            repos_found INTEGER,
            repos_added INTEGER,
            repos_updated INTEGER
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(
            name, description, readme_snippet, tags,
            content='', content_rowid='rowid'
        );
    ''')
    # Migrate existing DB: add summary column if missing
    cols = [r[1] for r in conn.execute('PRAGMA table_info(repos)').fetchall()]
    if 'summary' not in cols:
        conn.execute('ALTER TABLE repos ADD COLUMN summary TEXT')
    conn.commit()


def git_cmd(repo_path, *args):
    """Run a git command in a repo, return stdout or None."""
    try:
        result = subprocess.run(
            ['git', '-C', repo_path] + list(args),
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def get_remote_url(repo_path):
    return git_cmd(repo_path, 'config', '--get', 'remote.origin.url')


def get_default_branch(repo_path):
    ref = git_cmd(repo_path, 'symbolic-ref', 'refs/remotes/origin/HEAD', '--short')
    if ref:
        return ref.replace('origin/', '')
    # Fallback: check for main or master
    branches = git_cmd(repo_path, 'branch', '-l', '--format=%(refname:short)')
    if branches:
        for b in ['main', 'master']:
            if b in branches.split('\n'):
                return b
    return None


def get_last_commit(repo_path):
    return git_cmd(repo_path, 'log', '-1', '--format=%aI')


def extract_summary(content, repo_name):
    """
    Extract a 1-2 sentence TLDR from README content.
    Skips: title headings, badge lines, short lines, code blocks, HTML tags.
    Returns a string of up to 2 sentences, or None.
    """
    name_lower = repo_name.lower().replace('-', ' ').replace('_', ' ')
    lines = content.split('\n')
    in_code_block = False
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()

        # Track fenced code blocks
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_code_block = not in_code_block
            if current:
                paragraphs.append(' '.join(current))
                current = []
            continue
        if in_code_block:
            continue

        # Blank line = paragraph break
        if not stripped:
            if current:
                paragraphs.append(' '.join(current))
                current = []
            continue

        # Skip headings
        if stripped.startswith('#'):
            if current:
                paragraphs.append(' '.join(current))
                current = []
            continue

        # Skip badge/image lines
        if re.match(r'^\[?!\[', stripped):
            continue

        # Skip HTML tags
        if re.match(r'^<[^>]+>', stripped):
            continue

        # Skip horizontal rules
        if re.match(r'^[-=*_]{3,}$', stripped):
            continue

        # Strip inline markup: **bold**, _italic_, `code`, [text](url)
        clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', stripped)  # links
        clean = re.sub(r'[*_`]', '', clean)
        clean = re.sub(r'<[^>]+>', '', clean)
        clean = clean.strip()

        # Must be prose: at least 35 chars, contains a space, not just a URL
        if len(clean) < 35:
            continue
        if ' ' not in clean:
            continue
        if clean.startswith('http'):
            continue

        # Skip lines that are just the repo name / title reworded
        if clean.lower().replace('-', ' ').replace('_', ' ').startswith(name_lower):
            if len(clean) < len(name_lower) + 20:
                continue

        current.append(clean)

    if current:
        paragraphs.append(' '.join(current))

    if not paragraphs:
        return None

    # Take the first good paragraph, cap to 2 sentences
    para = paragraphs[0]
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', para)
    summary = ' '.join(sentences[:2])
    return summary[:300] if summary else None


def get_readme(repo_path):
    """Return (description, summary, full_snippet) from README."""
    for name in ['README.md', 'README.rst', 'README.txt', 'README']:
        p = os.path.join(repo_path, name)
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(4000)
                repo_name = os.path.basename(repo_path)
                summary = extract_summary(content, repo_name)
                # Legacy description: first meaningful line (kept for FTS)
                lines = content.split('\n')
                desc = None
                for line in lines:
                    clean = re.sub(r'^[#=\-\s\*>]+', '', line).strip()
                    clean = re.sub(r'[\[\]!]', '', clean).strip()
                    if len(clean) > 10:
                        desc = clean[:200]
                        break
                return desc, summary, content[:2000]
            except Exception:
                pass
    return None, None, None


def detect_languages(repo_path):
    """Scan top-level + one level deep for language indicators."""
    langs = Counter()
    try:
        for root, dirs, files in os.walk(repo_path):
            # Skip .git and vendor dirs
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', 'vendor', '__pycache__', '.venv', 'venv'}]
            depth = root.replace(repo_path, '').count(os.sep)
            if depth > 2:
                dirs.clear()
                continue
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in EXT_LANG:
                    langs[EXT_LANG[ext]] += 1
                # Special files
                if f == 'Dockerfile' or f.endswith('.dockerfile'):
                    langs['docker'] += 5
                elif f == 'Makefile':
                    langs['make'] += 1
                elif f == 'Vagrantfile':
                    langs['vagrant'] += 3
                elif f == 'Jenkinsfile':
                    langs['jenkins'] += 5
                elif f == '.github':
                    langs['github-actions'] += 3
                elif f == 'ansible.cfg' or f.endswith('.ansible.yml'):
                    langs['ansible'] += 5
    except PermissionError:
        pass
    # Return top languages (those with > 2 occurrences or special markers)
    return [lang for lang, count in langs.most_common(5) if count >= 2]


def extract_keyword_tags(readme_text):
    """Find known keywords in README content."""
    if not readme_text:
        return []
    text_lower = readme_text.lower()
    found = []
    for kw in KEYWORD_TAGS:
        # Match whole word
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, text_lower):
            found.append(kw)
    return found


def get_category_tag(repo_path):
    """Derive a tag from the parent directory name if it's a category folder."""
    parent = os.path.basename(os.path.dirname(repo_path))
    git_root_name = os.path.basename(GIT_ROOT)
    if parent != git_root_name:
        return parent.lower().replace(' ', '-')
    return None


def find_repos(base_path):
    """Walk the git directory and find all repos (up to 3 levels deep)."""
    repos = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules'}]
        depth = root.replace(base_path, '').count(os.sep)
        if depth > 2:
            dirs.clear()
            continue
        if '.git' in os.listdir(root) if root != base_path else False:
            repos.append(root)
            dirs.clear()  # Don't descend into sub-repos
    # Also check immediate children
    try:
        for entry in os.listdir(base_path):
            full = os.path.join(base_path, entry)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, '.git')):
                if full not in repos:
                    repos.append(full)
    except PermissionError:
        pass
    return repos


def scan_and_populate():
    """Main scan routine."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    init_db(conn)

    print(f"Scanning {GIT_ROOT}...")
    repo_paths = find_repos(GIT_ROOT)
    print(f"Found {len(repo_paths)} repositories (before dedup)")

    # Deduplicate: same remote URL = same repo in multiple folders
    url_map = {}  # url -> list of paths
    no_url = []
    for rpath in repo_paths:
        url = get_remote_url(rpath)
        if url:
            # Normalize URL for comparison
            norm = url.lower().rstrip('/').replace('.git', '')
            norm = norm.replace('git@github.com:', 'https://github.com/')
            norm = norm.replace('git@gitlab.com:', 'https://gitlab.com/')
            url_map.setdefault(norm, []).append(rpath)
        else:
            no_url.append(rpath)

    deduped = []
    url_dupe_groups = 0
    for norm_url, paths in url_map.items():
        if len(paths) == 1:
            deduped.append(paths[0])
        else:
            url_dupe_groups += 1
            # Prefer deepest path (most specific category folder)
            best = max(paths, key=lambda p: p.count(os.sep))
            deduped.append(best)

    # Dedup no-URL repos by name
    name_map = {}
    for rpath in no_url:
        name = os.path.basename(rpath)
        name_map.setdefault(name, []).append(rpath)
    name_dupe_groups = 0
    for name, paths in name_map.items():
        if len(paths) == 1:
            deduped.append(paths[0])
        else:
            name_dupe_groups += 1
            best = max(paths, key=lambda p: p.count(os.sep))
            deduped.append(best)

    dupe_groups = url_dupe_groups + name_dupe_groups
    removed = len(repo_paths) - len(deduped)
    if removed > 0:
        print(f"Deduped: {dupe_groups} repos have duplicates ({removed} extra copies removed), {len(deduped)} unique repos kept")
    repo_paths = deduped

    added = 0
    updated = 0

    for rpath in sorted(repo_paths):
        name = os.path.basename(rpath)
        url = get_remote_url(rpath)
        desc, summary, readme = get_readme(rpath)
        last_commit = get_last_commit(rpath)
        branch = get_default_branch(rpath)

        # Generate tags
        tags = set()
        lang_tags = detect_languages(rpath)
        tags.update(lang_tags)
        kw_tags = extract_keyword_tags(readme)
        tags.update(kw_tags)
        cat_tag = get_category_tag(rpath)
        if cat_tag:
            tags.add(cat_tag)

        # Upsert repo
        existing = conn.execute(
            'SELECT id FROM repos WHERE path = ?', (rpath,)
        ).fetchone()

        if existing:
            repo_id = existing[0]
            conn.execute('''
                UPDATE repos SET name=?, url=?, description=?, summary=?,
                readme_snippet=?, last_commit=?, default_branch=?,
                updated_at=datetime('now')
                WHERE id=?
            ''', (name, url, desc, summary, readme, last_commit, branch, repo_id))
            updated += 1
        else:
            cur = conn.execute('''
                INSERT INTO repos (name, path, url, description, summary,
                readme_snippet, last_commit, default_branch)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, rpath, url, desc, summary, readme, last_commit, branch))
            repo_id = cur.lastrowid
            added += 1

        # Replace auto-generated tags (preserve manual ones)
        conn.execute(
            "DELETE FROM repo_tags WHERE repo_id=? AND source='auto'",
            (repo_id,)
        )
        for tag in tags:
            conn.execute('''
                INSERT OR IGNORE INTO repo_tags (repo_id, tag, source)
                VALUES (?, ?, 'auto')
            ''', (repo_id, tag.lower()))

        # Update FTS
        tag_str = ' '.join(sorted(tags))
        conn.execute(
            'INSERT OR REPLACE INTO repo_fts(rowid, name, description, readme_snippet, tags) VALUES (?, ?, ?, ?, ?)',
            (repo_id, name, desc or '', readme or '', tag_str)
        )

    # Remove stale repos (in DB but not in current scan)
    scanned_paths = set(sorted(repo_paths))
    stale = conn.execute('SELECT id, path FROM repos').fetchall()
    stale_ids = [r[0] for r in stale if r[1] not in scanned_paths]
    if stale_ids:
        placeholders = ','.join('?' * len(stale_ids))
        # Get FTS data before deleting so we can remove from contentless FTS
        for sid in stale_ids:
            old = conn.execute('SELECT name, description, readme_snippet FROM repos WHERE id=?', (sid,)).fetchone()
            old_tags = conn.execute("SELECT GROUP_CONCAT(tag, ' ') FROM repo_tags WHERE repo_id=?", (sid,)).fetchone()
            if old:
                conn.execute(
                    "INSERT INTO repo_fts(repo_fts, rowid, name, description, readme_snippet, tags) VALUES('delete', ?, ?, ?, ?, ?)",
                    (sid, old[0] or '', old[1] or '', old[2] or '', old_tags[0] or '')
                )
        conn.execute(f'DELETE FROM repo_tags WHERE repo_id IN ({placeholders})', stale_ids)
        conn.execute(f'DELETE FROM repo_embeddings WHERE repo_id IN ({placeholders})', stale_ids)
        conn.execute(f'DELETE FROM repos WHERE id IN ({placeholders})', stale_ids)
        print(f"Removed {len(stale_ids)} stale repos")

    # Log the scan
    conn.execute('''
        INSERT INTO scan_log (repos_found, repos_added, repos_updated)
        VALUES (?, ?, ?)
    ''', (len(repo_paths), added, updated))

    conn.commit()
    conn.close()

    print(f"Done: {added} added, {updated} updated")
    print(f"DB: {DB_PATH}")


if __name__ == '__main__':
    scan_and_populate()
