#!/usr/bin/env python3
# repo-browser -- Semantic embedding generator via Ollama
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
Generate embeddings for repos via Ollama nomic-embed-text.
Stores float32 vectors in SQLite BLOB. Run after scan_repos.py.
"""

import os
import json
import shutil
import struct
import sqlite3
import urllib.request
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from rb_config import get_db_path

DB_PATH = get_db_path()
OLLAMA_URL = 'http://localhost:11434/api/embed'
MODEL = 'nomic-embed-text'


def embed_workers():
    """Concurrent Ollama embedding requests to run.

    Ollama on a GPU queues and batches internally, so more in-flight requests
    keep it fed; on CPU inference too many just thrash. GPU → 6, CPU → 3.
    """
    # ponytail: presence of the SMI binary is a good-enough GPU proxy; if it's
    # present but the GPU is broken, Ollama falls back to CPU and 6 workers is
    # merely a bit eager. Run the SMI query instead if that ever bites.
    has_gpu = bool(shutil.which('nvidia-smi') or shutil.which('xpu-smi'))
    return 6 if has_gpu else 3


def get_embedding(text):
    """Get embedding vector from Ollama."""
    payload = json.dumps({'model': MODEL, 'input': text}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data['embeddings'][0]


def vec_to_blob(vec):
    """Pack float list to bytes."""
    return struct.pack(f'{len(vec)}f', *vec)


def blob_to_vec(blob):
    """Unpack bytes to float list."""
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def build_embed_text(repo):
    """Combine repo fields into a single text for embedding."""
    parts = []
    parts.append(f"Repository: {repo['name']}")
    if repo['description']:
        parts.append(f"Description: {repo['description']}")
    if repo['tags']:
        parts.append(f"Tags: {repo['tags']}")
    if repo['readme_snippet']:
        # Truncate to ~500 chars for embedding
        parts.append(f"README: {repo['readme_snippet'][:500]}")
    return '\n'.join(parts)


def embed_all():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get repos needing embeddings (new or updated since last embed)
    rows = conn.execute('''
        SELECT r.id, r.name, r.description, r.readme_snippet, r.updated_at,
               GROUP_CONCAT(t.tag, ', ') as tags,
               e.updated_at as embed_updated
        FROM repos r
        LEFT JOIN repo_tags t ON r.id = t.repo_id
        LEFT JOIN repo_embeddings e ON r.id = e.repo_id
        GROUP BY r.id
        HAVING e.repo_id IS NULL
            OR r.updated_at > COALESCE(e.updated_at, '1970-01-01')
    ''').fetchall()

    total = len(rows)
    workers = embed_workers()
    print(f"Embedding {total} repos via {MODEL} ({workers} workers)...")

    def fetch(row):
        """Worker thread: call Ollama. Returns (id, name, vec-or-Exception)."""
        repo = dict(row)
        try:
            return repo['id'], repo['name'], get_embedding(build_embed_text(repo))
        except Exception as ex:   # pylint: disable=broad-exception-caught
            return repo['id'], repo['name'], ex

    # Ollama calls run in parallel; all DB writes stay on this thread since a
    # single sqlite3 connection isn't safe to share across threads.
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, row) for row in rows]
        for fut in as_completed(futures):
            repo_id, name, result = fut.result()
            if isinstance(result, Exception):
                print(f"  SKIP {name}: {result}")
                continue
            conn.execute('''
                INSERT OR REPLACE INTO repo_embeddings (repo_id, embedding, model, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            ''', (repo_id, vec_to_blob(result), MODEL))
            done += 1
            if done % 25 == 0 or done == total:
                conn.commit()
                print(f"  {done}/{total}")

    conn.commit()

    embedded = conn.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
    print(f"Done. {embedded} repos have embeddings.")
    conn.close()


if __name__ == '__main__':
    embed_all()
