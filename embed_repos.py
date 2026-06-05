#!/usr/bin/env python3
"""
Generate embeddings for repos via Ollama nomic-embed-text.
Stores float32 vectors in SQLite BLOB. Run after scan_repos.py.
"""

import os
import json
import struct
import sqlite3
import urllib.request
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'repos.db')
OLLAMA_URL = 'http://localhost:11434/api/embed'
MODEL = 'nomic-embed-text'


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
    print(f"Embedding {total} repos via {MODEL}...")

    for i, row in enumerate(rows):
        repo = dict(row)
        text = build_embed_text(repo)
        try:
            vec = get_embedding(text)
            blob = vec_to_blob(vec)
            conn.execute('''
                INSERT OR REPLACE INTO repo_embeddings (repo_id, embedding, model, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            ''', (repo['id'], blob, MODEL))

            if (i + 1) % 25 == 0 or i + 1 == total:
                conn.commit()
                print(f"  {i+1}/{total}")
        except Exception as ex:
            print(f"  SKIP {repo['name']}: {ex}")

    conn.commit()

    embedded = conn.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
    print(f"Done. {embedded} repos have embeddings.")
    conn.close()


if __name__ == '__main__':
    embed_all()
